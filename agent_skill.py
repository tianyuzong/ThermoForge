"""Prepare deterministic candidates and compile the Codex skill's small edits."""
import copy
import json
import re

from pydantic import ValidationError
from schemas import Heat, Simulation


def skill_root(engine):
    return engine.ROOT / '.agents' / 'skills' / 'thermal-config'


def draft_current(model_id, current):
    value = {**copy.deepcopy(current or {}), 'model_id': model_id}
    try:
        return Simulation.model_validate(value).model_dump(mode='json')
    except ValidationError:
        # A manually entered zero step or a heater awaiting its selection is
        # editable input, not a runnable configuration. Keep invalid/missing
        # required values for clarification; add only ordinary schema defaults.
        original = Simulation(model_id=model_id).model_dump(mode='json')
        original.update(value)
        for heat in original.get('heat_sources', []):
            if isinstance(heat, dict):
                for key, field in Heat.model_fields.items():
                    if key not in heat and not field.is_required():
                        heat[key] = field.get_default(call_default_factory=True)
        return original


def prepare(engine, model_id, prompt, current, conversation=None):
    if current and current.get('model_id', model_id) != model_id:
        raise ValueError('当前配置属于其他模型。')
    original = draft_current(model_id, current)
    from agent_conversation import rule_candidate, user_prompts
    candidate = rule_candidate(engine, model_id, prompt, original, conversation)
    geometry_request = '\n'.join(user_prompts(prompt, conversation))
    selectors = engine._surface_selections(model_id, engine._requested_selections(geometry_request))
    public_selectors = {key: {'face_count': value['face_count']} for key, value in selectors.items()
                        if not key.startswith('component:') or int(key.split(':')[1]) in
                        {int(n) for n in re.findall(r'(?:组件|部件)\s*(\d+)', geometry_request)}}

    def compact(config, prefix):
        result = copy.deepcopy(config)
        for field in ('heat_sources', 'cooling'):
            for index, group in enumerate(result.get(field, [])):
                faces = group.pop('faces', [])
                if not faces:
                    continue
                token = next((name for name, value in selectors.items() if value['faces'] == faces), None)
                if token is None:
                    token = f'{prefix}:{field}:{index}'
                    selectors[token] = dict(faces=faces, face_count=len(faces), name='保留现有选区')
                public_selectors[token] = {'face_count': len(faces)}
                group['surface_selection'] = token
        return result

    metadata = engine.read_json(engine.MODELS / model_id / 'metadata.json')
    public = dict(request=prompt, model_id=model_id,
                  geometry=dict(dimensions_m=metadata.get('dimensions_m'), bounds_m=metadata.get('bounds_m'),
                                components=[{k: r[k] for k in ('component_id', 'name', 'bounds_m') if k in r}
                                            for r in metadata.get('components', [])]),
                  current=compact(original, 'current'), candidate=compact(candidate['config'], 'candidate'),
                  available_selections=public_selectors,
                  rule_questions=candidate.get('questions', []), rule_warnings=candidate.get('warnings', []))
    if conversation is not None:
        public['conversation'] = copy.deepcopy(conversation)
    from agent_parameters import parameter_contract
    public['parameter_contract'] = parameter_contract()
    if re.search(r'中心|中间|中部|中央|正中', geometry_request):
        center = [(a+b)/2 for a,b in zip(*metadata['bounds_m'])]
        public['geometry'].update(center_m=center, center_in_solid=engine._point_in_solid(model_id, center))
        for component in public['geometry']['components']:
            if component.get('bounds_m') and str(component['component_id']+1) in re.findall(r'(?:组件|部件)\s*(\d+)', geometry_request):
                center = [(a+b)/2 for a,b in zip(*component['bounds_m'])]
                component.update(center_m=center, center_in_solid=engine._point_in_solid(model_id, center))
    return dict(model_id=model_id, original=original, candidate=candidate['config'],
                selectors=selectors, public=public, warnings=candidate.get('warnings', []))


def review_instructions(engine):
    text = (skill_root(engine) / 'SKILL.md').read_text(encoding='utf-8')
    instructions = text.split('<!-- prepared-review -->', 1)[1].split('<!-- /prepared-review -->', 1)[0].strip()
    return ('宿主已加载下列 thermal-config skill 说明并完成规则提取、几何计算。这是纯文本核对阶段：'
            '不要调用工具、读取或写入文件、运行脚本或再次加载技能；只输出修正 JSON。\n'
            + instructions)


def prompt_text(engine, prepared):
    return (review_instructions(engine) + '\n输入数据：\n'
            + json.dumps(prepared['public'], ensure_ascii=False, separators=(',', ':')))


def _check_no_faces(value):
    if isinstance(value, dict):
        if 'faces' in value:
            raise ValueError('面编号只能由本地选区映射生成；请使用 surface_selection。')
        for item in value.values():
            _check_no_faces(item)
    elif isinstance(value, list):
        for item in value:
            _check_no_faces(item)


def apply_patch_value(config, path, value):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('参数路径必须以 / 开头。')
    keys = [part.replace('~1', '/').replace('~0', '~') for part in path[1:].split('/')]
    if keys[0] not in Simulation.model_fields or keys[0] == 'model_id' or 'faces' in keys:
        raise ValueError('不允许修改该参数路径：' + path)
    _check_no_faces(value)
    target = config
    for key in keys[:-1]:
        if isinstance(target, list):
            if not key.isdigit() or int(key) >= len(target):
                raise ValueError('参数数组位置不存在：' + path)
            target = target[int(key)]
        elif isinstance(target, dict) and key in target:
            target = target[key]
        else:
            raise ValueError('参数路径不存在：' + path)
    last = keys[-1]
    if isinstance(target, list):
        if last == '-':
            target.append(value)
        elif last.isdigit() and int(last) < len(target):
            target[int(last)] = value
        else:
            raise ValueError('参数数组位置不存在：' + path)
    elif isinstance(target, dict):
        target[last] = value
        if last == 'surface_selection':
            target.pop('faces', None)
        if last == 'source_type' and value == 'point':
            target['faces'] = []
    else:
        raise ValueError('参数路径不可修改：' + path)


def compile_review(engine, prepared, text):
    review = engine._json_from_text(text)
    if not isinstance(review, dict) or set(review) != {'patch', 'questions'}:
        raise ValueError('Skill 必须返回 patch 和 questions。')
    if not isinstance(review['patch'], list) or not isinstance(review['questions'], list):
        raise ValueError('patch 与 questions 必须为数组。')
    if len(review['patch']) > 64 or any(not isinstance(q, str) for q in review['questions']):
        raise ValueError('Skill 返回了无效修正或问题。')
    config = copy.deepcopy(prepared['candidate'])
    for edit in review['patch']:
        if not isinstance(edit, dict) or set(edit) != {'path', 'value_json'} or not isinstance(edit['value_json'], str):
            raise ValueError('每项修正必须包含 path 和 value_json。')
        value = json.loads(edit['value_json'])
        apply_patch_value(config, edit['path'], value)
    if review['questions']:
        return None, review['questions']
    for group in [*config.get('heat_sources', []), *config.get('cooling', [])]:
        selector = group.pop('surface_selection', None)
        if selector is not None:
            selection = prepared['selectors'].get(selector)
            if not selection or not selection['faces']:
                raise ValueError(f'当前模型没有可用的“{selector}”选区。')
            group['faces'] = selection['faces'].copy()
    # Reuse the same final Pydantic and geometry validation as other providers.
    validated = engine._validated_codex_text(json.dumps(config), prepared['original'], prepared['model_id'])
    return validated, []
