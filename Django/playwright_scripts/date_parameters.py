"""Small declarative relative-date parameters, shared with standalone downloads."""
import re
from datetime import date, timedelta

DATE_PARAMETER = re.compile(r'^\{\{date:([+-]?\d{1,4}):(iso|day|day2|month|month2|year|weekday_zh|weekday_zh_short|weekday_iso)\}\}$')


def resolve_parameters(parameters, today=None):
    today = today or date.today()
    result = dict(parameters)
    for key, value in parameters.items():
        if not isinstance(value, str) or not value.startswith('{{date:'):
            continue
        match = DATE_PARAMETER.fullmatch(value)
        if not match:
            raise ValueError('相对日期参数格式无效：' + str(key))
        offset, fmt = match.groups()
        if not -3660 <= int(offset) <= 3660:
            raise ValueError('相对日期偏移超出十年范围。')
        target = today + timedelta(days=int(offset))
        values = {'iso': target.isoformat(), 'day': str(target.day), 'day2': f'{target.day:02d}',
                  'month': str(target.month), 'month2': f'{target.month:02d}', 'year': str(target.year),
                  'weekday_zh': '星期' + '一二三四五六日'[target.weekday()],
                  'weekday_zh_short': '周' + '一二三四五六日'[target.weekday()],
                  'weekday_iso': str(target.isoweekday())}
        result[key] = values[fmt]
    return result
