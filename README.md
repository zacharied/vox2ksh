# vox2ksh

This converts `.vox` charts to `.ksh` charts. If you don't know what those are then this repo is not for you.

## Prerequisites

Copy `config.sample.ini` to `config.ini` and edit the copy to contain the necessary paths.

Run all scripts while in repo root directory, **not** while in the `src` directory.

The paths of the chart files and associated data must match the following requirements. Note that the `extractor.py`
script will extract song audio, preview audio, and jacket artwork with the proper naming schemes.

#### Charts

These should be in the format given by running [ifstools](https://github.com/mon/ifstools) on the game's vox IFS files.
Upon extraction, the files will be in a number of `vox_<xx>_ifs` directories. Move all vox files within those 
directories into the one specified by the `--vox-dir` flag. There should be no subdirectories within that directory; a 
path to a chart would be `<vox-dir>/<a chart>.vox`.

#### Songs & previews

Previews and song audio are stored in separate files. The KSH format does not support separate preview audio files, so
we employ a workaround: attach the preview audio to the end of the song audio following a short period of silence. With
a correct `po` (preview offset) in the KSH file, the preview will play correctly, but the chart will end early enough
that playback stops before the preview. 

First, arrange your song audio and preview audio in two separate directories with the naming scheme given by the
`extractor.py` script. Then use the `prepare_audio.py` script. It searches the first argument for song audio files and
the second argument for preview files, concatenates them with the correct amount of silence, and then outputs an OGG
file with the same name as the input files to the third argument.

#### FX Chip Sounds

Place these files in the directory specified by the `--fx-chip-sound-dir` flag. They should be named in the same order 
they came out of the IFS archive, which can be found in the same directory as the song audio. They should be in the WAV
format.

*TODO: Extract FX chip sounds in extractor.*

#### Jackets

Jackets belong in the directory specified by the `--jacket-dir` flag. Their names should follow the format 
`<song id>_<difficulty index>`, where the `difficulty index` is its 1-indexed position in the difficulty order
(**NOV**, **ADV**, **EXH**, **INF**, **MXM**). So, the **ADV** jacket for the song with ID 501 would be `501_2.png`.

#### Metadata

Place the `music_db.xml` in the directory specified by your config. It can be found in the same directory as the source 
IFS files.

*TODO: Extract music DB files in extractor.*

## Usage

Just running `converter.py` with Python 3 should begin converting the charts. The `--testcase` argument can be used to
convert a specific testcase (run it with no argument to list available testcases). The `--song-id` argument can be used
to convert the song with the specified ID. Run `converter.py -h` to see all options, including their short forms.

## Other

This software is provided for educational purposes only.