
import os
import glob
import click
from collections import Counter
from datetime import date
from utils import get_data_dir, load_yaml, dump_obj, get_settings, role_is_active
from merge import compare_objects, merge_people
from retire import retire


def expand_seats(state):
    settings = get_settings()[state]
    expanded = {}
    for chamber in ['upper', 'lower', 'legislature']:
        spec = settings.get(chamber + "_seats")
        if not spec:
            continue
        if isinstance(spec, int):
            counts = {d: 1 for d in range(1, spec + 1)}
        elif isinstance(spec, list):
            counts = {d: 1 for d in spec}
        elif isinstance(spec, dict):
            counts = spec
        else:
            raise Exception("Unrecognized specification %r" % spec)

        expanded[chamber] = Counter(counts)

    for vacancy in settings.get('vacancies', []):
        if date.today() < vacancy['vacant_until']:
            expanded[vacancy['chamber']][vacancy['district']] -= 1

    return expanded


class PersonFile(object):
    def __init__(self, filename, data):
        assert os.path.exists(filename)
        self.filename = filename
        if 'data/' in filename:
            self.kind = 'existing'
        elif 'retired/' in filename:
            self.kind = 'retired'
        elif 'incoming/' in filename:
            self.kind = 'incoming'
        else:
            raise Exception("Can't determine kind from filename %s" % filename)
        self.data = data

    @classmethod
    def from_yaml(cls, filename):
        with open(filename) as f:
            data = load_yaml(f)
        return cls(filename, data)

    @classmethod
    def from_dir(cls, directory):
        return [PersonFile.from_yaml(filename) for filename in
                glob.glob(os.path.join(directory, "*.yml"))]

    @property
    def id(self):
        return self.data['id']

    @property
    def name(self):
        return self.data['name']

    @property
    def seat(self):
        role = self.data['roles'][-1]
        district = role['district']
        return role['type'], int(district) if district.isdigit() else district

    def differences(self, other, ignore_keys=set(["id"])):
        return compare_objects(self.data, other.data, ignore_keys=ignore_keys)

    def same_name(self, other):
        return self.name == other.name  # TODO: Levenshtein dist

    def merge(self, other):
        "Merge differences from the other PersonFile into this one"
        self.data = merge_people(self.data, other.data, keep_on_conflict='new',
                                 keep_both_ids=False)

    def save(self):
        dump_obj(self.data, filename=self.filename)


def deferred(fn):
    def add_operation(self, *a, **kw):
        if self.defer:
            self.operations.append((fn, a, kw))
        else:
            return fn(self, *a, **kw)
    return add_operation


class PersonMerger(object):
    def __init__(self, defer=True, save=True):
        self.operations = []
        self.defer = defer
        self.save = save

    def sort_operations(self):
        self.operations.sort(key=lambda op: getattr(op[1][0], 'seat'))

    def execute_deferred(self):
        self.sort_operations()
        while self.operations:
            fn, a, kw = self.operations.pop(0)
            fn(self, *a, **kw)

    @deferred
    def create(self, new):
        click.secho(f"In {new.seat} creating {new.name}.", fg='green')
        if self.save:
            assert 'incoming' in new.filename
            new.filename = new.filename.replace('incoming/', 'data/')
            new.save()

    @deferred
    def retire(self, existing):
        click.secho(f"In {existing.seat} retiring {existing.name}.", fg='blue')
        if self.save:
            end_date = date.today().strftime('%Y-%m-%d')  # FIXME
            retire(end_date, existing.filename, None, False)

    @deferred
    def update(self, existing, new):
        moving = ""

        # end any active roles
        for role in existing.data['roles']:
            district = role['district']
            seat = role['type'], int(district) if district.isdigit() else district
            if role_is_active(role) and seat != new.seat:
                role['end_date'] = date.today().strftime('%Y-%m-%d')  # FIXME
                moving = f" and moving to {new.seat}"

        click.secho(f"In {existing.seat} updating "
                    f"{existing.name}{moving}.", fg='yellow')

        if self.save:
            existing.merge(new)
            existing.save()


def merge(state, merger):
    # list all the existing and new people by district
    # if two person-files have the same name, and are in the same district,
    # it's an UPDATE: update the old file from the new, and remove these files
    # from further consideration

    # Then, check there are people with the same names but in different
    # districts. These people should be UPDATEd to a new district. It's
    # functionally the same as above, but we need a different process to find
    # them.

    # If we find the existing person for whom we are doing an UPDATE is retired
    # we need to un-retire that person; that is, move their yaml file from
    # /retired/ to /data/.

    # If there was a vacant district and there is now a new person for that
    # district, CREATE that person. This involves moving the yaml file from
    # /incoming/ to /data/.

    # Finally, if a district had one person in it, and now has another, we need
    # to RETIRE the existing person and CREATE (or MOVE) the new person.
    # To RETIRE, we move the person from /data/ to /retired/.

    # FIXME: handle multi-member districts
    # FIXME: handle vacancies

    data_dir = get_data_dir(state)
    existing_people = PersonFile.from_dir(os.path.join(data_dir, 'people')) + \
        PersonFile.from_dir(os.path.join(data_dir, 'retired'))
    incoming_dir = data_dir.replace('data', 'incoming')
    assert data_dir != incoming_dir
    new_people = PersonFile.from_dir(os.path.join(incoming_dir, 'people'))

    seats = expand_seats(state)
    handled = set()

    for existing in existing_people:
        if existing.id in handled:
            continue

        for new in new_people:
            if new.id in handled:
                continue

            if existing.same_name(new):
                if existing.differences(new):
                    merger.update(existing, new)
                handled |= {existing.id, new.id}
                seats[new.seat[0]][new.seat[1]] -= 1
                break

    for existing in existing_people:
        if existing.id in handled:
            continue
        merger.retire(existing)

    for new in new_people:
        if new.id in handled:
            continue
        merger.create(new)

    merger.execute_deferred()


@click.command()
@click.argument('state', default=None)
@click.option('--defer/--no-defer', default=True, help="Defer changes until all are ready.")
@click.option('--save/--no-save', default=True, help="Save changes.")
def entrypoint(state, defer, save):
    merger = PersonMerger(defer=defer, save=save)
    merge(state, merger)


if __name__ == '__main__':
    entrypoint()
