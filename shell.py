import imp
import struct
import subprocess
import sys
import threading
import traceback

import audio

sample_rate = 44100

if 0:
    import pyaudio
    p = pyaudio.PyAudio()
    #print(p.get_device_count())
    #for i in range(p.get_device_count()):
    #    print(p.get_device_info_by_index(i))

    stream = p.open(format=pyaudio.paInt16,
            output_device_index=1,
            channels=2,
            rate=sample_rate,
            output=True)
else:
    p = subprocess.Popen(['sox', '-q', '-r', '44100', '-b', '16', '-e',
            'signed-integer', '-c', '2', '-t', 'raw', '--buffer', '128', '-', '-d'],
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

tr = InputThread()
tr.daemon=True
tr.start()

ctx = audio.Context()
ctx.store('sample_rate', sample_rate)

sample = 0
last_audio = audio
while True:
    try:
        sample += 1
        ctx.store('sample', sample)
        for channel in [0, 1]:
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
