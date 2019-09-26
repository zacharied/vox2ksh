#!/usr/bin/env python3.7
# WATCH OUT: This code is bad and ugly

from pydub import AudioSegment
from threading import Thread
import xml.etree.ElementTree as ET
import os, sys
import glob

if len(sys.argv) != 4:
    print('Please supply arguments in the form:\nprepare_audio.py [song sources] [preview sources] [output directory]',
          file=sys.stderr)
    sys.exit(1)

workpool = [[] for _ in range(3)]

for i, song in enumerate(glob.glob(f'{sys.argv[1]}/*.ogg')):
    workpool[i % len(workpool)].append(song)

def process_pool(songpool):
    for song in songpool:
        process_song(song)

def process_song(song):
    song_id = os.path.splitext(os.path.basename(song))[0].split('_')[0]
    print(f'>> Processing {song_id}.')
    if os.path.exists(f'{sys.argv[3]}/{os.path.basename(song)}'):
        print('Already exists, skipping.')
        return

    audio = AudioSegment.from_file(song)
    audio = audio.append(AudioSegment.silent(duration=150000-len(audio)), crossfade=0)
    audio = audio.append(AudioSegment.from_file(f'{sys.argv[2]}/{song_id}.ogg'), crossfade=0)
    audio = audio.apply_gain(-audio.max_dBFS)
    print(f'Max dBFS: {audio.max_dBFS}')
    audio.export(f'{sys.argv[3]}/{os.path.basename(song)}', format='ogg', parameters=['-q:a', '7'])

threadpool = [Thread(target=process_pool, args=(workpool[i],)) for i in range(3)]
for thread in threadpool:
    thread.start()

for thread in threadpool:
    thread.join()
