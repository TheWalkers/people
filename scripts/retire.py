#!/usr/bin/env python
import os
import glob
import click
from utils import load_yaml, dump_obj, role_is_active


def retire_from_committee(committee, person_id, end_date):
    num = 0
    for role in committee['memberships']:
        if role.get('id') == person_id and role_is_active(role):
            role['end_date'] = end_date
            num += 1
    return committee, num


def retire_person(person, end_date, reason=None, death=False):
    num = 0
    for role in person['roles']:
        if role_is_active(role):
            role['end_date'] = end_date
            if reason:
                role['end_reason'] = reason
            num += 1

    if death:
        person['death_date'] = end_date

    return person, num


def move_file(filename):        # pragma: no cover
    new_filename = filename.replace('/people/', '/retired/')
    click.secho(f'moved from {filename} to {new_filename}')
    os.renames(filename, new_filename)


def retire(end_date, filename, reason, death):
    """
    Retire a legislator, given END_DATE and FILENAME.

    Will set end_date on active roles & committee memberships.
    """
    # end the person's active roles & re-save
    with open(filename) as f:
        person = load_yaml(f)
    if death:
        reason = "Deceased"
    person, num = retire_person(person, end_date, reason, death)
    dump_obj(person, filename=filename)

    # same for their committees
    committee_glob = os.path.join(os.path.dirname(filename), '../organizations/*.yml')
    for com_filename in glob.glob(committee_glob):
        with open(com_filename) as f:
            committee = load_yaml(f)
        committee, num_roles = retire_from_committee(committee, person['id'], end_date)
        dump_obj(committee, filename=com_filename)
        num += num_roles

    if num == 0:
        click.secho('no active roles to retire', fg='red')
    elif num == 1:
        click.secho(f'retired person')
    else:
        click.secho(f'retired person from {num} roles')

    move_file(filename)

@click.command()
@click.argument('end_date')
@click.argument('filename')
@click.option('--reason', default=None)
@click.option('--death', is_flag=True)
def entrypoint(end_date, filename, reason, death):
    return retire(end_date, filename, reason, death)

if __name__ == '__main__':
    entrypoint()
