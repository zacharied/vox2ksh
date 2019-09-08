#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import glob
import re
import os, subprocess
from shutil import copyfile, rmtree

# Replace this with the location of your game's `contents` directory.
# Example:
#   GAME_DIR = 'G:/Games/SDVX/contents'
GAME_DIR = ''

# Two-element arrays: the first element is the location of the data folder. The second is a boolean: it should be True
# if the directory is from a layeredFS mod, otherwise False.
# Example:
#   DATA_DIRS = [['data_mods/omnimix', True], ['data_mods/vividwave', True], ['data', False]]
DATA_DIRS = []

# These are the output directories. Set these to wherever you want.
# Example:
#   OUT_SONGS = 'D:/SDVX-Extract/song/wav'
#   OUT_PREVIEW = 'D:/SDVX-Extract/preview/wav'
#   OUT_JACKET = 'D:/SDVX-Extract/jacket'
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

            song_file = None
            if weird:
                try:
                    song_file = next(iter(glob.glob(f'{data_dir[0]}/sound/{child.find("info/label").text}_*.2dx')))
                except StopIteration: pass
            if song_file is None:
                song_file = next(iter(glob.glob(f'{data_dir[0]}/sound/???_{str(song_id).zfill(4)}_*.2dx')))
            
            subprocess.call(['C:/Tools/2dxDump.exe', song_file])

            print('> Copying song.')
            copyfile('0.wav', f'{OUT_SONGS}/{song_id}.wav')
            if os.path.exists('1.wav') and not os.path.exists('2.wav'):
                print('> Copying INF song.')
                copyfile('1.wav', f'{OUT_SONGS}/{song_id}_inf.wav')
            clear_dir('.')
            
            preview_file = next(iter(glob.glob(f'{data_dir[0]}/sound/preview/???_{str(song_id).zfill(4)}_*.2dx')))
            subprocess.call(['C:/Tools/2dxDump.exe', preview_file])
            print('> Copying preview.')
            copyfile('0.wav', f'{OUT_PREVIEW}/{song_id}.wav')
            if os.path.exists('1.wav') and not os.path.exists('2.wav'):
                print('> Copying INF preview.')
                copyfile('1.wav', f'{OUT_PREVIEW}/{song_id}_inf.wav')
            clear_dir('.')

            preview_files = glob.glob(f'{data_dir[0]}/graphics/jk/jk_???_{str(song_id).zfill(4)}_?_b.ifs')
            for preview_file in preview_files:
                subprocess.call(['ifstools', '--tex-only', preview_file])
                preview_file = os.path.basename(preview_file)
                jacket_base = re.sub(r'\.ifs$', '', preview_file)
                jacket_idx = re.sub(r'jk_00[0-9]_[0-9]{4}_([1-9])_b.ifs', r'\1', preview_file)
                print(f'> Copying jacket {jacket_base}.')
                copyfile(f'{jacket_base}_ifs/{jacket_base}.png', f'{OUT_JACKET}/{song_id}_{jacket_idx}.png')
                clear_dir('.')