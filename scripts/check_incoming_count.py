import os
from glob import glob
from utils import get_settings, get_districts


RATIO = 0.9


def expected_people(state):
    expected = 0
    settings = get_settings()[state]
    for v in get_districts(settings).values():
        expected += sum(v.values())
    return expected


def incoming_people(state):
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '../incoming', state, 'people'))
    return len(glob(path + '/*.yml'))


def check_state(state):
    expected = expected_people(state)
    existing = incoming_people(state)
    return RATIO < abs(existing/expected) < (1 / RATIO)


if __name__ == '__main__':
    import sys
    state = sys.argv[1]
    valid = check_state(state)
    if not valid:
        print(state, " is not valid")
    sys.exit(not valid)
