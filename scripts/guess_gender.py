import boto3
import click
import gender_guesser.detector
import nameparser
import requests
from multiprocessing import Pool
from utils import load_yaml, dump_obj
from io import BytesIO
from PIL import Image


REKOGNITION_MAX_SIZE = 5242880
SCALE_FACTOR = 0.9

name_guesser = gender_guesser.detector.Detector(case_sensitive=False)


face_guesser = boto3.client('rekognition', region_name='us-west-2')


class Gender(object):
    Male = "Male"
    Female = "Female"
    Nonbinary = "Nonbinary"
    Unknown = "Unknown"


def get_first_name(legislator):
    given = legislator.get('given_name')
    if given:
        return given

    parsed = nameparser.HumanName(legislator['name'])
    return parsed.first


def get_name_gender(legislator):
    first_name = get_first_name(legislator)
    guess = name_guesser.get_gender(first_name, 'usa')

    # try again if parser accidentally found a middle name/initial
    if guess == "unknown" and ' ' in first_name:
        first_name = first_name.split(' ')[0]
        guess = name_guesser.get_gender(first_name, 'usa')

    return guess, first_name


def get_face_gender(legislator):
    gender_face, gender_face_confidence = Gender.Unknown, 0

    photo_url = legislator.get('image')
    if not photo_url:
        return gender_face, gender_face_confidence

    photo_data = ''
    try:
        headers = {}

        # Indiana needs a User-Agent
        if 'state:in' in legislator['roles'][-1]['jurisdiction']:
            headers['User-Agent'] = 'openstates'

        # VA Senate SSL is screwed up, use plain HTTP
        photo_url = photo_url.replace(
            'https://apps.lis.virginia.gov', 'http://apps.lis.virginia.gov')

        resp = requests.get(photo_url, timeout=10, verify=False,
                            headers=headers, proxies=PROXIES)
        photo_data = resp.content

        #  convert GIFs to PNGs - silly AWS service can't do GIFs
        if (('.gif' in photo_url.lower()) or
                len(photo_data) > REKOGNITION_MAX_SIZE):

            infile = BytesIO(resp.content)
            img = Image.open(infile)
            img = img.convert('RGB')

            out = BytesIO()
            img.save(out, format='jpeg')

            photo_data = out.getvalue()

            while len(photo_data) > REKOGNITION_MAX_SIZE:
                w, h = img.size
                w = int(SCALE_FACTOR * w)
                h = int(SCALE_FACTOR * h)
                click.secho(f"{photo_url} is too big ({len(photo_data)}b)! "
                            f"Resizing to {w}x{h}", fg='yellow')
                img = img.resize((w, h))
                out = BytesIO()
                img.save(out, format='jpeg')

                photo_data = out.getvalue()

    except Exception as e:
        # problems getting photos are common
        click.secho("Problem fetching or converting: %r" % e, fg='red')

    else:
        if resp.status_code == requests.codes.ok:
            try:
                resp = face_guesser.detect_faces(Image={'Bytes': photo_data},
                                                 Attributes=['ALL'])
                face_guess = resp['FaceDetails'][0]['Gender']
                gender_face = face_guess['Value']
                assert gender_face in (Gender.Female, Gender.Male)
                gender_face_confidence = float(face_guess['Confidence'])
            except Exception as e:
                # facial recognition won't be 100% - bad format is
                # a common problem we can't easily fix
                click.secho("Problem detecting: %r" % e, fg='red')
        else:
            click.secho(f"Got {resp.status_code} fetching {photo_url}", fg="red")

    return gender_face, gender_face_confidence


def get_gender(legislator):

    name_gender, first_name = get_name_gender(legislator)

    face_gender, face_confidence = get_face_gender(legislator)

    gender = Gender.Unknown

    if name_gender in ('female', 'mostly_female'):
        gender = Gender.Female
    elif name_gender in ('male', 'mostly_male'):
        gender = Gender.Male

    # reconciliation logic
    if (
            # name is unknown/ambiguous
            (name_gender in ('andy', 'unknown') and
             face_confidence > 50) or

            # take the face guess if name guess wasn't confident
            (name_gender in ('mostly_male', 'mostly_female') and
             face_confidence > 75) or

            # take a very confident face guess over name guess
            (face_confidence >= 99)):

        gender = face_gender

    click.echo(
        f'{legislator["name"]:28s}: {gender:6s} - '
        f'name {first_name:10s} : {name_gender} ; '
        f'face {legislator.get("image", "n/a")} : '
        f'{face_gender} ({face_confidence:.2f})')

    return gender


def update_legislator(legislator_file):
    legislator = load_yaml(open(legislator_file))

    if legislator.get('gender') and not OVERWRITE:
        click.secho(f'{legislator_file} already has a gender, skipping', fg='yellow')
        return

    gender = get_gender(legislator)
    if gender != Gender.Unknown:
        legislator['gender'] = gender
        dump_obj(legislator, filename=legislator_file)
    else:
        click.secho(f'{legislator_file} gender unknown', fg='yellow')


OVERWRITE = False
PROXIES = {}


@click.command()
@click.argument('legislator_files', type=click.Path(exists=True), nargs=-1)
@click.option('--overwrite', is_flag=True, help='overwrite existing gender value')
@click.option('--serial', is_flag=True, help='process files in serial')
@click.option('--proxy', multiple=True,
              help='pass image requests through a proxy. Specify by protocol, '
                   'like http:http://proxy.domain:1234/')
def entrypoint(legislator_files, overwrite, serial, proxy):
    global OVERWRITE
    global PROXIES

    OVERWRITE = overwrite

    for proxy_spec in proxy:
        PROXIES.update([tuple(proxy_spec.split(':', 1))])

    if serial:
        for legislator_file in legislator_files:
            update_legislator(legislator_file)
    else:
        pool = Pool()
        for result in pool.imap_unordered(update_legislator, legislator_files):
            pass


if __name__ == '__main__':
    entrypoint()
