"""Deterministic parameter extraction shared by the local parser and Codex skill.

Rules produce a candidate, not a claim that arbitrary natural language was fully
understood. The skill reviews that candidate against the original request.
"""
import copy
import re

from schemas import PRESETS, Simulation

NUMBER = r'-?\d+(?:\.\d+)?'
TIME_UNIT = r'小时|分钟|秒|min|h|s'
POWER = re.compile(r'(?<![\d.])(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>kW|千瓦|W|瓦)(?![A-Za-z])(?!(?:\s*[/／]))', re.I)
MATERIALS = [('不锈钢|stainless', 4), ('碳钢|steel', 3),
             ('铝|aluminium|aluminum', 1), ('铜|copper', 0), ('铁|iron', 2)]
ORDINALS = {value:index for index,value in enumerate(('一','二','三','四','五','六','七','八','九','十','十一','十二','十三','十四','十五','十六'))}
COMMAND = re.compile(r'不(?:再)?(?:启用|开启|使用|考虑)|不要|关闭|禁用|取消|启用|开启|打开|使用|考虑')


def seconds(value, unit):
    return float(value) * (3600 if unit.lower() in ('小时', 'h') else 60 if unit.lower() in ('分钟', 'min') else 1)


def duration(text):
    match = re.search(r'(?:仿真|计算|运行)(?:总时长|时长|时间|持续)?\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    if not match:
        match = re.search(r'(?:总时长|仿真时长)\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    return seconds(*match.groups()) if match else None


def time_window(text):
    match = re.search(r'(?:从|在)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')?\s*(?:到|至|[-~～–—])\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    if match:
        a, u, b, v = match.groups()
        return seconds(a, u or v), seconds(b, v)
    start = re.search(r'(' + NUMBER + r')\s*(' + TIME_UNIT + r')\s*(?:后|时)?\s*(?:开始|启动|开启|打开)', text, re.I)
    end = re.search(r'(' + NUMBER + r')\s*(' + TIME_UNIT + r')\s*(?:后|时)?\s*(?:停止|结束|关闭|关掉)', text, re.I)
    if not start:
        start = re.search(r'(?:开始|启动|开启)(?:时间)?\s*(?:设为|改为|为|=)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    if not end:
        end = re.search(r'(?:结束|停止|关闭)(?:时间)?\s*(?:设为|改为|为|=)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    return (seconds(*start.groups()) if start else None, seconds(*end.groups()) if end else None)


def clauses(text):
    return [part.strip() for part in re.split(r'[，,。;；\n]+', text) if part.strip()]


def heat_context(text):
    # Cooling clauses must not change a heater's selection or power.
    text = re.sub(r'(?:并且|同时|并|而)(?=[^，,。;；\n]{0,30}(?:对流|散热|换热系数))', '；', text)
    return '，'.join(part for part in clauses(text)
                    if not re.search(r'对流|散热|换热系数|环境温度|辐射', part)
                    and not (re.search(r'(?:组件|部件)\s*\d+', part)
                             and material(part) and not POWER.search(part)))


def feature_value(text, feature):
    value = None
    for clause in clauses(text):
        for match in re.finditer(feature, clause, re.I):
            commands = list(COMMAND.finditer(clause[:match.start()]))
            if commands:
                word = commands[-1].group()
                value = word in ('启用', '开启', '打开', '使用', '考虑')
    return value


def material(text):
    for pattern, index in MATERIALS:
        if re.search(pattern, text, re.I):
            return copy.deepcopy(PRESETS[index])
    return None


def make_plan(engine, model_id, prompt, current):
    model = engine.read_json(engine.MODELS / model_id / 'metadata.json')
    cfg = copy.deepcopy(current or {})
    cfg['model_id'] = model_id
    changes, warnings, questions = [], [], []
    prompt = prompt.replace('摄氏度', '°C').replace('摄氏', '°C')
    parts = clauses(prompt)
    base_text = '，'.join(p for p in parts if not re.search(r'(?:组件|部件)\s*\d+', p)
                         and not re.search(r'基础材料.*(?:不变|保持)', p))
    preset = material(base_text)
    if preset:
        cfg['base_material'] = preset
        changes.append(f'基础材料: {preset["name"]}')

    assignments = {x['component_id']: copy.deepcopy(x) for x in cfg.get('component_materials', [])}
    component_ids = {x['component_id'] for x in model.get('components', [])}
    for clause in parts:
        match = re.search(r'(?:组件|部件)\s*(\d+)', clause)
        preset = material(clause) if match else None
        if match and preset:
            index = int(match.group(1)) - 1
            if index not in component_ids:
                questions.append(f'当前模型没有组件 {index + 1}。')
                continue
            assignments[index] = dict(component_id=index, material=preset)
            changes.append(f'组件 {index+1} 材料: {preset["name"]}')
    if assignments:
        cfg['component_materials'] = list(assignments.values())

    if re.search(r'稳态|steady|最终稳定', prompt, re.I):
        cfg['analysis_mode'] = 'steady'
    elif re.search(r'瞬态|transient|升温|降温|冷却过程', prompt, re.I):
        cfg['analysis_mode'] = 'transient'

    total = duration(prompt)
    old_duration = cfg.get('duration_s', 3600)
    if total is not None:
        cfg['duration_s'] = total
        changes.append(f'仿真时长: {total:g} s')
        for source in cfg.get('heat_sources', []):
            if source.get('end_s') == old_duration:
                source['end_s'] = total

    for key, label, pattern in (
        ('initial_C', '初始温度', r'(?:初始|起始)温度\s*(?:设为|均为|为|=|:|：)?\s*(' + NUMBER + r')\s*°?C'),
        ('ambient_C', '环境温度', r'(?:环境温度|室温)\s*(?:设为|均为|为|=|:|：)?\s*(' + NUMBER + r')\s*°?C'),
        ('default_h', '换热系数', r'(?:换热系数|对流系数|\bh)\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')'),
        ('mesh_size_m', '网格尺寸', r'(?:网格(?:尺寸|大小)?|mesh)\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(mm|毫米|cm|厘米|m|米)'),
        ('dt_s', '计算步长', r'(?:计算|时间)?步长\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')'),
        ('save_s', '保存间隔', r'(?:保存|输出)(?:间隔)?\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')'),
        ('contact_resistance_m2K_W', '接触热阻', r'接触热阻\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r'(?:[eE][+-]?\d+)?)'),
    ):
        match = re.search(pattern, prompt, re.I)
        if not match:
            continue
        value = float(match.group(1))
        if key == 'mesh_size_m':
            unit = match.group(2).lower()
            value *= .001 if unit in ('mm', '毫米') else .01 if unit in ('cm', '厘米') else 1
        if key in ('dt_s', 'save_s'):
            value = seconds(value, match.group(2))
        cfg[key] = value
        changes.append(f'{label}: {value:g}')
    every = re.search(r'每\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')\s*(?:保存|输出)', prompt, re.I)
    if every:
        cfg['save_s'] = seconds(*every.groups())
    both = re.search(r'初始温度(?:和|与|、)环境温度\s*(?:均为|均|都为|为)?\s*(' + NUMBER + r')\s*°?C', prompt)
    if both:
        cfg['initial_C'] = cfg['ambient_C'] = float(both.group(1))

    heat_text = heat_context(prompt)
    from agent_heat import update_sources
    update_sources(engine, model_id, heat_text, cfg, changes, warnings, questions)
    sources = cfg['heat_sources']

    for field, pattern in [('radiation_enabled', r'辐射|radiation'), ('air_gap_enabled', r'空气间隙|间隙耦合')]:
        value = feature_value(prompt, pattern)
        if value is not None:
            cfg[field] = value
            changes.append(f'{pattern.split("|")[0]}: {"开启" if value else "关闭"}')
    if feature_value(prompt, r'相变') is False:
        for m in [cfg.get('base_material', {}), *[x['material'] for x in cfg.get('regions', [])],
                  *[x['material'] for x in cfg.get('component_materials', [])]]:
            m['phase_change'] = None
    for field, pattern, empty in [('power_profile', r'功率曲线', []), ('thermostat', r'温控', None)]:
        if feature_value(prompt, pattern) is False:
            for source in sources:
                source[field] = copy.deepcopy(empty)
    if re.search(r'对流|换热|散热', prompt):
        if feature_value(prompt, r'对流|散热') is False:
            cfg['default_h'] = 0
            cfg['heat_convection'] = False
        elif re.search(r'(?:包括|包含|也.*(?:对流|散热)|参与).*(?:受热面|热源面)|(?:受热面|热源面).*(?:也|参与|对流|散热)', prompt):
            cfg['heat_convection'] = not bool(re.search(r'(?:不包括|不包含|排除).*(?:受热面|热源面)|(?:受热面|热源面).*(?:不参与|不散热)', prompt))

    if not sources:
        questions.append('草案缺少热源：请指定功率与受热面，或点热源位置。')
    if cfg.get('mesh_size_m', .035) < max(model['dimensions_m']) / 130:
        warnings.append('已保留指定网格尺寸；较细网格可能增加计算时间和内存。')
    try:
        validated = Simulation.model_validate(cfg).model_dump(mode='json')
        engine._validate_geometry(model_id, validated)
    except (ValueError, KeyError) as error:
        questions.append('参数组合需要调整：' + str(error).split('\n')[0])
        validated = cfg
    return dict(ok=not questions, config=validated, changes=changes, warnings=warnings, questions=questions)
