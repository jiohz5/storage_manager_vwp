"""한국어/영어 UI 문자열 카탈로그.

폐쇄망이라 gettext 도구 체인(`msgfmt` 등)이나 외부 번역 라이브러리를 쓸 수
없으므로, 표준 라이브러리만으로 충분한 단순 dict 카탈로그를 쓴다. 키는 안정된
식별자이고 값은 `str.format` 템플릿이다.

설계 메모:
- 없는 키는 예외를 던지지 않고 "현재 언어 -> 기본 언어(한국어) -> 키 자체"
  순서로 되돌아간다. 번역 하나가 빠졌다고 GUI 전체가 죽으면 안 되고, 대신
  화면에 키가 그대로 보여서 빠진 것을 바로 알 수 있다.
- 언어 설정은 `config.json`의 `settings.language`에 저장하고, 프로세스 시작
  시 `set_language`로 한 번 적용한다. 전역 상태를 쓰는 이유는 등급 라벨처럼
  GUI가 아닌 계층(`tiers.py`)에서도 번역이 필요하기 때문이다.
- 파일에 기록되는 값(알림 JSON, 보고서)에는 언어 중립 코드(`tier` 등)를 항상
  함께 남긴다 - 나중에 다른 언어로 다시 렌더링할 수 있어야 하기 때문.
"""

from __future__ import annotations

from typing import Dict, List

from .locales import en, ko

KOREAN = "ko"
ENGLISH = "en"
DEFAULT_LANGUAGE = KOREAN

LANGUAGE_NAMES = {
    KOREAN: "한국어 (KOR)",
    ENGLISH: "English (ENG)",
}

# 문자열 자체는 언어별 파일에 있다 (`smvwp/locales/`).
_CATALOG: Dict[str, Dict[str, str]] = {
    KOREAN: ko.STRINGS,
    ENGLISH: en.STRINGS,
}

_current_language = DEFAULT_LANGUAGE


def available_languages() -> List[str]:
    return list(_CATALOG.keys())


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language, language)


def is_supported(language: str) -> bool:
    return language in _CATALOG


def set_language(language: str) -> str:
    """현재 언어를 바꾸고 실제로 적용된 언어를 반환한다.

    지원하지 않는 값이면 조용히 기본 언어로 되돌린다 - 설정 파일에 잘못된
    값이 들어 있어도 앱이 뜨지 않는 일은 없어야 한다."""

    global _current_language
    _current_language = language if is_supported(language) else DEFAULT_LANGUAGE
    return _current_language


def get_language() -> str:
    return _current_language


def t(key: str, **kwargs) -> str:
    """키를 현재 언어 문자열로 바꾼다. 없으면 기본 언어, 그것도 없으면 키 자체.

    `str.format` 인자가 모자라거나 남아도 예외를 던지지 않는다 (번역 문자열의
    사소한 불일치로 화면이 죽는 것보다 원문이라도 보이는 편이 낫다)."""

    template = _CATALOG.get(_current_language, {}).get(key)
    if template is None:
        template = _CATALOG.get(DEFAULT_LANGUAGE, {}).get(key)
    if template is None:
        return key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template
