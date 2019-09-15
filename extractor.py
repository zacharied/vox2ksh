#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import glob
import re
import os, subprocess
from shutil import copyfile, rmtree

# Replace this with the location of your game's `contents` directory.
# Example:
# GAME_DIR = 'G:/Games/SDVX/contents'
GAME_DIR = ''

# Two-element arrays: the first element is the location of the data folder. The second is a boolean: it should be True
# if the directory is from a layeredFS mod, otherwise False.
# Example:
# DATA_DIRS = [['data_mods/omnimix', True], ['data_mods/vividwave', True], ['data', False]]
DATA_DIRS = []

# These are the output directories. Set these to wherever you want.
# Example:
# OUT_SONGS = 'D:/SDVX-Extract/song/wav'
# OUT_PREVIEW = 'D:/SDVX-Extract/preview/wav'
# OUT_JACKET = 'D:/SDVX-Extract/jacket'
OUT_SONGS = ''
OUT_PREVIEW = ''
OUT_JACKET = ''

############################################################
# Script begins here. Do not edit anything past this line. #
############################################################

# TODO Documentation here.

if GAME_DIR == '' or len(DATA_DIRS) == 0 or OUT_SONGS == '' or OUT_PREVIEW == '' or OUT_JACKET == '':
    print('You have not set the parameters. Edit the lines at the top of the script to match your game '
          'installation and desired output locations.',
          file=os.sys.stderr)

def clear_dir(path):
    for f in os.listdir(path):
        try:
            os.remove(f'{path}/{f}')
        except PermissionError:
            rmtree(f'{path}/{f}')

def process_diff_2dx(song_file, song_id):
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

try:
    os.mkdir('_temp')
except FileExistsError:
    pass

os.chdir('_temp')

for data_dir in DATA_DIRS:
    data_dir[0] = f'{GAME_DIR}/{data_dir[0]}'
    music_db = f'{data_dir[0]}/others/{"music_db.merged.xml" if data_dir[1] == True else "music_db.xml"}'
    with open(music_db, encoding='shift_jisx0213') as db_file:
        tree = ET.fromstring(db_file.read())
        for child in tree:
            song_id = child.attrib['id']
            print(f'> Processing {song_id}.')
            weird = re.search(r'[a-z]', child.find('info/label').text) is not None

            song_files = []
            if weird:
                try:
                    for song_file in glob.glob(f'{data_dir[0]}/sound/{child.find("info/label").text}_*.2dx'):
                        song_files.append(song_file)
                except StopIteration: pass
            if len(song_files) == 0:
                for song_file in glob.glob(f'{data_dir[0]}/sound/???_{str(song_id).zfill(4)}_*.2dx'):
                    song_files.append(song_file)

            for song_file in song_files:
                subprocess.call(['C:/Tools/2dxDump.exe', song_file])

                # Certain songs store their extra INF audio as another file in the 2dx archive. However, old-style
                # charts always have 8 files in the 2dx archive, so we check if there's EXACTLY 2 in the archive to
                # see if it's an INF audio.
                if os.path.exists('1.wav') and not os.path.exists('2.wav'):
                    output_filename = f'{song_id}_inf.wav'
                else:
                    output_filename = process_diff_2dx(song_file, song_id)

                print(f'> Copying song to "{output_filename}".')
                copyfile('0.wav', f'{OUT_SONGS}/{output_filename}')

                clear_dir('.')
            
            preview_files = list(glob.glob(f'{data_dir[0]}/sound/preview/???_{str(song_id).zfill(4)}_*.2dx'))
            for preview_file in preview_files:
                subprocess.call(['C:/Tools/2dxDump.exe', preview_file])
                if os.path.exists('1.wav') and not os.path.exists('2.wav'):
                    output_filename = f'{song_id}_inf.wav'
                else:
                    output_filename = process_diff_2dx(preview_file, song_id)
                print(f'> Copying preview to "{output_filename}".')
                copyfile('0.wav', f'{OUT_PREVIEW}/{output_filename}')
                clear_dir('.')

            jacket_files = glob.glob(f'{data_dir[0]}/graphics/jk/jk_???_{str(song_id).zfill(4)}_?_b.ifs')
            for jacket_file in jacket_files:
                subprocess.call(['ifstools', '--tex-only', jacket_file])
                jacket_file = os.path.basename(jacket_file)
                jacket_base = re.sub(r'\.ifs$', '', jacket_file)
                jacket_idx = re.sub(r'jk_00[0-9]_[0-9]{4}_([1-9])_b.ifs', r'\1', jacket_file)
                print(f'> Copying jacket {jacket_base}.')
                copyfile(f'{jacket_base}_ifs/{jacket_base}.png', f'{OUT_JACKET}/{song_id}_{jacket_idx}.png')
                clear_dir('.')