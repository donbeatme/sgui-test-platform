"""Keep explicit coverage omissions blocking even if a model labels them warnings.

This is a conservative consistency check, not proof of semantic completeness.
Original version data stays immutable; API and worker use the same current check.
"""
import re
import json
from .contract import validate
from .acceptance import basic_version

OMISSION = re.compile(
    r'无法(?!访问|连接)[^。；，\n]{0,60}(?:断言|验证|定位)'
    r'|(?:未对|没有对)[^。；\n]{0,100}(?:断言|验证|核验)'
    r'|(?:未做|未进行|没有进行|没有做)[^。；，\n]{0,40}(?:断言|验证|核验)'
    r'|(?:未覆盖|未实现|漏测|跳过验证|跳过断言|省略断言)'
    r'|(?:缺少|缺失|遗漏)[^。；，\n]{0,50}(?:断言|定位器|步骤|预期|验证)'
    r'|(?:missing|omitted|skipped|cannot verify|unable to verify)[^.;\n]{0,60}(?:assertion|locator|step|verification|expectation)',
    re.I,
)


NETWORK_REQUIREMENT = re.compile(
    r'抓包|网络层|网络请求层|请求(?:数量|次数|计数)|(?:网络|HTTP|接口)[^。；\n]{0,16}(?:计数|数量|次数|请求体|响应码)'
    r'|network|request count|response code', re.I)
NETWORK_LIMITATION = re.compile(
    r'(?:未做|未进行|没有进行)(?:网络|HTTP)(?:请求)?(?:层|级)?(?:计数|数量)(?:验证|断言|核验)')


def separate_notes(blockers, warnings, snapshot=None):
    blockers = list(blockers)
    notes = []
    for warning in warnings:
        checked = warning
        if snapshot is not None and not NETWORK_REQUIREMENT.search(json.dumps(snapshot, ensure_ascii=False)):
            # UI-only stop boundaries do not implicitly require packet capture.
            # Ignore just that technical limitation, not any other missing check.
            checked = NETWORK_LIMITATION.sub('', checked)
        if OMISSION.search(checked):
            blockers.append(warning)
        else:
            notes.append(warning)
    return list(dict.fromkeys(blockers)), list(dict.fromkeys(notes))


def version_review(version):
    if version.origin == 'guided':
        return list(dict.fromkeys([*version.issues, *validate(version.code, version.script.snapshot,
            version.parameters, require_assertions=not basic_version(version))])), version.warnings
    return separate_notes([*version.issues, *validate(version.code, version.script.snapshot, version.parameters)],
                          version.warnings, version.script.snapshot)
