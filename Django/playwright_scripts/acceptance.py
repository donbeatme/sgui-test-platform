"""Persist acceptance per build/version; never reinterpret historical results."""
REPLAY_ONCE = 'replay_once'


def reduced_motion_version(version):
    if version.origin != 'guided':
        return False
    if not hasattr(version, '_reduced_motion'):
        lists = version.script.builds.filter(observation__motion_policy='reduced').values_list(
            'observation__acceptance_versions', flat=True)
        version._reduced_motion = any(version.pk in (ids or []) for ids in lists)
    return version._reduced_motion


def basic_build(build):
    return (build.observation or {}).get('acceptance') == REPLAY_ONCE


def basic_version(version):
    if version.origin != 'guided':
        return False
    if not hasattr(version, '_basic_acceptance'):
        # Fetch only the small version lists, not the captured DOM/history.
        lists = version.script.builds.filter(observation__acceptance=REPLAY_ONCE).values_list(
            'observation__acceptance_versions', flat=True)
        version._basic_acceptance = any(version.pk in (ids or []) for ids in lists)
    return version._basic_acceptance


def required_replays(version):
    return 1 if version.origin != 'guided' or basic_version(version) else 2


def replay_verified(version):
    return version.runs.filter(status='pass', result__status='pass').count() >= required_replays(version)
