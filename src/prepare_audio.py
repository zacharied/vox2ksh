#!/usr/bin/env python3.7
# WATCH OUT: This code is bad and ugly

import argparse
import configparser
import glob
import pickle
import os
from xml.etree import ElementTree
from threading import Thread

from pydub import AudioSegment

argparser = argparse.ArgumentParser()
argparser.add_argument('-c', '--num-cores', type=int, default=3)
argparser.add_argument('-e', '--src-audio-extension', default='ogg')
argparser.add_argument('-n', '--normalize', action='store_true', dest='do_normalize',
                       help='Normalize the audio. Not needed in most cases since volume is handled in the chart '
                            'metadata.')
args = argparser.parse_args()

config = configparser.ConfigParser()
config.read("config.ini")

song_source_dir = config['Directories']['song_audio_source_dir']
preview_source_dir = config['Directories']['preview_audio_source_dir']
output_dir = config['Directories']['combined_song_preview_audio_dir']
db_source_dir = config['Directories']['music_db_source_dir']
preview_offset = int(config['Audio']['hidden_preview_position']) * 1000

print(f'Inserting previews at {preview_offset}ms.')

db_entries = []
for db_file in glob.glob(f'{db_source_dir}/*.xml'):
    with open(db_file, encoding='shift_jisx0213') as file:
        db_entries += ElementTree.fromstring(file.read())

workpool = [[] for _ in range(args.num_cores)]
for i, songfile in enumerate(glob.glob(f'{song_source_dir}/*.{args.src_audio_extension}')):
    workpool[i % len(workpool)].append(songfile)

volume_chart = []

def process_pool(songpool):
    for song in songpool:
        song_id = os.path.splitext(os.path.basename(song))[0].split('_')[0]

        # Find volume tag for chart data.
        try:
            chart_entry = int(
                next(filter(lambda entry: entry.attrib['id'] == song_id, db_entries)).find('info/volume').text)
        except StopIteration:
            chart_entry = None

        print(f'>> Processing {song_id}.')
        if os.path.exists(f'{output_dir}/{os.path.basename(song)}'):
            print('Already exists, skipping.')
            continue

        # Append silence followed by the preview.
        audio = AudioSegment.from_file(song)
        audio = audio.append(AudioSegment.silent(duration=preview_offset-len(audio)), crossfade=0) \
            .append(AudioSegment.from_file(f'{preview_source_dir}/{song_id}.{args.src_audio_extension}'), crossfade=0)
        if chart_entry is not None:
            # Add to graph.
            chart_entry = (chart_entry, audio.max_dBFS)
            volume_chart.append(chart_entry)
        if args.do_normalize:
            audio = audio.apply_gain(-audio.max_dBFS)

        audio.export(f'{output_dir}/{os.path.basename(song)}', format='ogg', parameters=['-q:a', '7'])

threadpool = [Thread(target=process_pool, args=(workpool[i],)) for i in range(args.num_cores)]
for thread in threadpool:
    thread.start()
for thread in threadpool:
    thread.join()

with open('out/volume_chart.bin', 'wb') as file:
    pickle.dump(volume_chart, file, pickle.HIGHEST_PROTOCOL)
