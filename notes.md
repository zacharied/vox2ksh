# .vox format notes

## Tracks

There are 9 tracks, but track 9 is never(?) used.

Each track has 4 columns.

Column 1 is the form `<measure>,<beat>,<offset(?)>`.

Each beat consists of 48 "units".

### Track 1

Blue laser. 

C2 defines where the contour point is: 0 is the far left and 127 is the far right.

C3 take one of three values: (0) continuing a laser, (1) starting a laser, or (2) ending a laser.

C4 defines something; if a C4 is (1), then the next line
will be the other end of the slam.

C5 is something; it seems to usually be 0.

C6 defines range; 

### Track 2

FX-L.

C2 defines the length of the note in units. It is 0 if it's a chip.

C3 is the index of the effect(?). It is 255 if it is a chip.

### Track 3

BT-A.

C2 defines the length of the not in units. It's 0 if it's a chip.

C3 is ????. It is 2 on a hold.

### Track 4

BT-B.

Follows same rules as track 3.

### Track 5

BT-C.

### Track 6

BT-D.

### Track 7

FX-R.

### Track 8

Red laser.

Follows same rules as track 1.
__
# KSH format notes

Laser location is determined by `<letter's ASCII value> - 48`. there are 64 possible positions, so `o` is the highest (farthest right).