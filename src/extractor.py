#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import glob
import re
import os, subprocess
import configparser
from shutil import copyfile, rmtree

config = configparser.ConfigParser()
config.read("config.ini")

song_output_dir = config['Directories']['song_audio_source_dir']
preview_output_dir = config['Directories']['preview_audio_source_dir']
jacket_output_dir = config['Directories']['jacket_source_dir']
db_output_dir = config['Directories']['music_db_source_dir'] # TODO Extract music DB.
game_contents_dir = config['Directories']['game_contents_dir']
path_to_2dxdump = config['Utilities']['app_2dxdump_path']

def clear_dir(path):
    for f in os.listdir(path):
        try:
            os.remove(f'{path}/{f}')
        except PermissionError:
            rmtree(f'{path}/{f}')

def wavname_from_2dx(song_file, song_id):
    if song_file.endswith('_1n.2dx'):
        return f'{song_id}_nov.wav'
    elif song_file.endswith('_2a.2dx'):
        return f'{song_id}_adv.wav'
    elif song_file.endswith('_3e.2dx'):
        return f'{song_id}_exh.wav'
    elif song_file.endswith('_4i.2dx'):
        return f'{song_id}_inf.wav'
    elif song_file.endswith('_5m.2dx'):
        return f'{song_id}_mxm.wav'
    return f'{song_id}.wav'

def extract_data_dir(data_dir: str, is_mod_data: bool):
    music_db = f'{data_dir}/others/{"music_db.merged.xml" if is_mod_data == True else "music_db.xml"}'
    with open(music_db, encoding='shift_jisx0213') as db_file:
        tree = ET.fromstring(db_file.read())
        for child in tree:
            song_id = child.attrib['id']
            print(f'> Processing {song_id}.')

            song_files = []
            # "weird" songs have a label field like "1n0001" or whatever.
            # They have their audio files in a different naming format than most, starting with label instead of game
            #  ID.
            if re.search(r'[a-z]', child.find('info/label').text) is not None:
                try:
                    for song_file in glob.glob(f'{data_dir}/sound/{child.find("info/label").text}_*.2dx'):
                        song_files.append(song_file)
                except StopIteration: pass
            if len(song_files) == 0:
                for song_file in glob.glob(f'{data_dir}/sound/???_{str(song_id).zfill(4)}_*.2dx'):
                    song_files.append(song_file)

            for song_file in song_files:
                subprocess.call([path_to_2dxdump, song_file])

                # Certain songs store their extra INF audio as another file in the 2dx archive. However, old-style
                # charts always have 8 files in the 2dx archive, so we check if there's EXACTLY 2 in the archive to
                # see if it's an INF audio.
                if os.path.exists('1.wav') and not os.path.exists('2.wav'):
                    # Actually, some songs follow that pattern but have no INF.
                    if child.find('info/inf_ver').text != '0':
                        print(f'> Copying INF song to "{song_id}_inf.wav".')
                        copyfile('1.wav', f'{song_output_dir}/{song_id}_inf.wav')

                output_filename = wavname_from_2dx(song_file, song_id)

                print(f'> Copying song to "{output_filename}".')
                copyfile('0.wav', f'{song_output_dir}/{output_filename}')

                clear_dir('.')

            preview_files = list(glob.glob(f'{data_dir}/sound/preview/???_{str(song_id).zfill(4)}_*.2dx'))
            for preview_file in preview_files:
                subprocess.call(['C:/Tools/2dxDump.exe', preview_file])

                if os.path.exists('1.wav') and not os.path.exists('2.wav'):
                    print(f'> Copying INF preview to "{song_id}_inf.wav".')
                    copyfile('1.wav', f'{song_output_dir}/{song_id}_inf.wav')

                output_filename = wavname_from_2dx(preview_file, song_id)

                print(f'> Copying preview to "{output_filename}".')
                copyfile('0.wav', f'{preview_output_dir}/{output_filename}')
                clear_dir('.')

            jacket_files = glob.glob(f'{data_dir}/graphics/jk/jk_???_{str(song_id).zfill(4)}_?_b.ifs')
            for jacket_file in jacket_files:
                subprocess.call(['ifstools', '--tex-only', jacket_file])
                jacket_file = os.path.basename(jacket_file)
                jacket_base = re.sub(r'\.ifs$', '', jacket_file)
                jacket_idx = re.sub(r'jk_00[0-9]_[0-9]{4}_([1-9])_b.ifs', r'\1', jacket_file)
                print(f'> Copying jacket {jacket_base}.')
                copyfile(f'{jacket_base}_ifs/{jacket_base}.png', f'{jacket_output_dir}/{song_id}_{jacket_idx}.png')
                clear_dir('.')

try:
    os.mkdir('_temp')
except FileExistsError:
    pass

os.chdir('_temp')

extract_data_dir('data', False)

game_mod_data_dir = f'{game_contents_dir}/data_mods'
if os.path.isdir(game_mod_data_dir):
    for mod_dir in os.listdir(game_mod_data_dir):
        extract_data_dir(mod_dir, True)
