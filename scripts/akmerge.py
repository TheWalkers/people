
import os
import glob
import click
from collections import Counter, OrderedDict
from datetime import date
from difflib import SequenceMatcher
from operator import itemgetter
from utils import get_data_dir, load_yaml, dump_obj, get_settings, role_is_active
from merge import compare_objects, merge_people, ListDifference, ItemDifference
from retire import retire

SIMILAR_NAME_RATIO = 0.7


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


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

    def differences(self, other, ignore_keys=set(["id"]), new_only=True):
        differences = compare_objects(self.data, other.data, ignore_keys=ignore_keys)
        if new_only:
            differences = [
                diff for diff in differences if
                ((isinstance(diff, ListDifference) and diff.which_list == 'second') or
                 (isinstance(diff, ItemDifference) and diff.value_two is not None))]

        return differences

    def merge_contact_details(self, old, new, difference):
        contacts = OrderedDict((o['note'], o) for o in old[difference.key_name])

        for office in new[difference.key_name]:
            contacts.setdefault(office['note'], {}).update(office)

        old[difference.key_name] = list(contacts.values())


    def merge(self, other):
        "Merge differences from the other PersonFile into this one"
        custom_merges = {
            'contact_details': self.merge_contact_details,
        }
        self.data = merge_people(self.data, other.data, keep_on_conflict='new',
                                 custom_merges=custom_merges)


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
    def __init__(self, defer=True, save=True, end_date=None):
        self.operations = []
        self.defer = defer
        self.save = save
        self.end_date = end_date or date.today().strftime('%Y-%m-%d')

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
            retire(self.end_date, existing.filename, None, False)

    @deferred
    def update(self, existing, new):
        moving = ""

        # end any active roles
        for role in existing.data['roles']:
            district = role['district']
            seat = role['type'], int(district) if district.isdigit() else district
            if role_is_active(role) and seat != new.seat:
                role['end_date'] = self.end_date
                moving = f" and moving to {new.seat}"

        click.secho(f"In {existing.seat} updating "
                    f"{existing.name}{moving}.", fg='yellow')

        if self.save:
            existing.merge(new)
            existing.save()


def merge(state, merger):
    """
    Merge incoming data for a given state into existing files.

    Matches existing people to new people by name. If names match, update
    the existing person. For unmatched people, retire existing persons and
    create new persons.
    """
    data_dir = get_data_dir(state)
    existing_people = PersonFile.from_dir(os.path.join(data_dir, 'people')) + \
        PersonFile.from_dir(os.path.join(data_dir, 'retired'))
    incoming_dir = data_dir.replace('data', 'incoming')
    assert data_dir != incoming_dir
    new_people = PersonFile.from_dir(os.path.join(incoming_dir, 'people'))

    handled = set()

    similar = []

    for existing in existing_people:
        if existing.id in handled:
            continue

        for new in new_people:
            if new.id in handled:
                continue

            if existing.name == new.name:
                if existing.differences(new, new_only=True):
                    merger.update(existing, new)
                handled |= {existing.id, new.id}
                break

            elif existing.seat == new.seat:  # check for similar name, same seat
                name_similarity = similarity(existing.name, new.name)
                if name_similarity > SIMILAR_NAME_RATIO:
                    similar.append((name_similarity, existing, new))

    similar.sort(key=itemgetter(0), reverse=True)
    for _, existing, new in similar:
        if existing.id in handled or new.id in handled:
            continue
        if existing.differences(new, new_only=True):
            merger.update(existing, new)
        handled |= {existing.id, new.id}

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
@click.option('--end-date', default=None, help="Default end date for retirements and moves.")
def entrypoint(state, defer, save, end_date):
    merger = PersonMerger(defer=defer, save=save, end_date=end_date)
    merge(state, merger)


if __name__ == '__main__':
    entrypoint()
