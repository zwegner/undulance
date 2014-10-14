import imp
import struct
import subprocess
import sys
import threading
import traceback

sample_rate = 44100
channels = 1

export = None
if len(sys.argv) > 1:
    export_samples = float(sys.argv.pop(1)) * sample_rate
    export = sys.argv.pop(1)
assert len(sys.argv) == 1

if 0:
    import pyaudio
    p = pyaudio.PyAudio()
    #print(p.get_device_count())
    #for i in range(p.get_device_count()):
    #    print(p.get_device_info_by_index(i))

    stream = p.open(format=pyaudio.paInt16,
            output_device_index=1,
            channels=channels,
            rate=sample_rate,
            output=True)
else:
    args = ['sox', '-q', '-r', '44100', '-b', '16', '-e',
            'signed-integer', '-c', str(channels), '-t', 'raw',
            '--buffer', '512', '-']
    if export:
        ftype = export.partition('.')[2]
        args += ['-t', ftype, export]
    else:
        args += ['-d']
    p = subprocess.Popen(args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    stream = p.stdin

class InputThread(threading.Thread):
    def run(self):
        global last_audio
        while True:
            try:
                input('> ')
                last_audio = audio
                imp.reload(audio)
            except EOFError:
                sys.exit()
            except:
                traceback.print_exc()
                continue

if not export:
    tr = InputThread()
    tr.daemon=True
    tr.start()

import audio

ctx = audio.Context()
ctx.store('sample_rate', sample_rate)

sample = 0
last_audio = audio
while not export or sample < export_samples:
    try:
        sample += 1
        ctx.store('sample', sample)
        for channel in range(channels):
            ctx.reset()
            ctx.store('channel', channel)
            value = int(audio.eq.eval(ctx) * (65535 / 20.))
            # Hard clipping? Hard clipping.
            value = max(-32768, min(32767, value))
            stream.write(struct.pack('h', value))
        stream.flush()
    except KeyboardInterrupt:
        sys.exit()
    except:
        if audio == last_audio:
            raise
        audio = last_audio
