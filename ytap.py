#!/bin/python
from __future__ import unicode_literals
import re
import sys
import shlex
import select
import signal
import requests
import argparse
import youtube_dl
from time import sleep
from youtube_dl.utils import *
from datetime import datetime
from keybind import KeyBinder
from subprocess import Popen

# Helpers
def log_sep():
    print('=' * 100)

LOG_INFO = 0
LOG_WARN = 1
LOG_CRIT = 2
LOG_ICONS = {
    LOG_INFO: '*',
    LOG_WARN: '!',
    LOG_CRIT: 'X',
}
def log(msg, level=LOG_INFO):
    icon = LOG_ICONS.get(level, LOG_INFO)
    print('[%s] %s' % (icon, msg))

def debug():
    print('state: %r' % state)
    print('IS_STATE_PLAY: %r' % is_state(STATE_PLAY))
    print('IS_STATE_FIND: %r' % is_state(STATE_FIND))
    print('IS_STATE_NEXT: %r' % is_state(STATE_NEXT))

def print_menu():
    log_sep()
    log('0. Terminate program')
    log('1. Terminate player')
    log('2. Youtube search')
    log('3. Debug')
    log_sep()

def notify(msg):
    if not args.notify:
        return

    cmd = args.notify.format(message=shlex.quote(msg))
    Popen(cmd, shell=True, stdout=None, stderr=None)

def terminate_process(process):
    if process is None:
        return False

    ret = process.poll()
    if ret is None:
        process.terminate()
    else:
        return False

    return True

def str2bool(v):
    if isinstance(v, bool):
       return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

# Args
parser = argparse.ArgumentParser(description='Youtube Audio Player')
parser.add_argument('--video',
        type=str2bool, nargs='?',
        const=True, default=False,
        help='Also play video')
parser.add_argument('--notify',
        help='Send notifications. Eg. ./ytda.py --notify "notify-send \'{message}\'"')

args = parser.parse_args()

# Keybinds
def do_debug():
    debug()

def do_menu():
    menu_select()

KeyBinder.activate({
    'Ctrl-K': do_debug,
    'Ctrl-M': do_menu,
}, run_thread=True)

# Consts
search_text = '[*] Search (-1 for menu): '
select_action_text = '[*] Select action: '

cookie_file = '/tmp/ytda.cookie.jar'

# State
STATE_PLAY = 1<<1
STATE_FIND = 1<<2
STATE_NEXT = 1<<3
state = {
    '_': STATE_FIND,
    'next_video': None,
    'next_url': None,
    'player': None,
    'mpv_stdout': open('/tmp/mpv.out', 'w'),
    'mpv_stderr': open('/tmp/mpv.err', 'w'),
    'history_dict': {},
    'history': [],
}

def set_state(new_state, key='_'):
    state[key] = new_state

def get_state(key='_'):
    return state[key]

def is_state(flag):
    return (state['_'] & flag) == flag

def add_to_history(vid):
    state['history'].append({ 'id': vid, 'time': datetime.now() })
    state['history_dict'][vid] = len(state['history']) - 1

def was_played(vid):
    played_index = state['history_dict'].get(vid, False)
    if not played_index:
        return (False, None)

    return (True, state['history'][played_index])

# Actions
ACTION_TERM_PROGRAM = 0
ACTION_TERM_PLAYER = 1
ACTION_FIND = 2
ACTION_DEBUG = 3

def menu_select():
    print_menu()
    action = int_or_none(input(select_action_text))
    if action == ACTION_TERM_PROGRAM:
        terminate_process(get_state('player'))
        get_state('mpv_stdout').close()
        get_state('mpv_stderr').close()
        sys.exit()
    elif action == ACTION_TERM_PLAYER:
        terminate_process(get_state('player'))
    elif action == ACTION_FIND:
        set_state(STATE_FIND)
    elif action == ACTION_DEBUG:
        debug()

# YoutubeDL
class MyLogger(object):
    def debug(self, msg):
        print(msg)

    def warning(self, msg):
        print(msg)

    def error(self, msg):
        print(msg)


def my_hook(d):
    if d['status'] == 'finished':
        print('Done downloading, now converting ...')


ydl_opts = {
    'format': 'bestvideo+bestaudio' if args.video else 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'logger': MyLogger(),
    'progress_hooks': [my_hook],
    'simulate': True,
    'cookiefile': cookie_file,
}

def fetch_next_url(video):
    page_url = video.get('webpage_url')
    res = requests.get(page_url)

    # Get next autoplay
    m = re.search('data-secondary-video-url="/watch\?v=([^&"]+)', res.text)
    vid = m.group(1) if not m is None else None

    # Get all videos
    vids = list(set(re.findall('/watch\?v=([^&"\\\]+)', res.text)))
    (played, _) = was_played(vid)
    if played or vid is None:
        if vid in vids:
            vids.remove(vid)
        vid = random.choice(vids)

    next_url = 'https://www.youtube.com/watch?v=%s' % vid

    log('Fetching next url: %s' % next_url)
    next_video = get_video(next_url)

    title = next_video.get('title')
    log('Next title: %s' % title)
    set_state(next_url, 'next_url')
    set_state(next_video, 'next_video')

def play(video, stdout=None, stderr=None):
    vid = video.get('id')
    url = video.get('url')
    title = video.get('title')
    format_id = video.get('format_id')

    # Add to history
    add_to_history(vid)

    # Notify changes
    msg = 'Now playing: %s' % title
    log(msg)
    notify(msg)

    mpv_args = ['mpv']

    if not url is None:
        mpv_args += [url]
    else:
        (video_id, audio_id) = format_id.split('+')
        formats = video.get('formats')

        audio_url = None
        video_url = None
        for v in formats:
            if v.get('format_id') == video_id:
                video_url = v.get('url')
            elif v.get('format_id') == audio_id:
                audio_url = v.get('url')
        mpv_args += [video_url, '--audio-file=%s' % audio_url]


    proc = Popen(mpv_args, stdout=stdout, stderr=stderr)
    log('Player started with pid %r' % proc.pid)

    fetch_next_url(video)

    set_state(get_state() | STATE_PLAY)

    return proc

def search(search_term):
    num = None
    term = None
    try:
        (term, num) = search_term.split('|')
    except ValueError:
        term = search_term
    num = num if num else '3'
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        response = ydl.extract_info('ytsearch%s:%s' % (num, term))

        log_sep()
        entries = try_get(response, lambda x: x['entries'], list) or []
        for i, video in enumerate(entries):
            title = video.get('title')
            webpage_url = video.get('webpage_url')
            thumbnail = video.get('thumbnail')
            duration = float_or_none(video.get('duration'))
            view_count = int_or_none(video.get('view_count'))
            log('[%d] title: %s' % (i, title))
            log('[%d] thumbnail: %s' % (i, thumbnail))
            log('[%d] webpage_url: %s' % (i, webpage_url))
            log('[%d] duration: %s' % (i, duration))
            log('[%d] view_count: %s' % (i, view_count))
            log_sep()

        vid_num = len(entries)
        if vid_num == 1:
            return entries[0]

        while True:
            vid_num = int(input('[*] Pick (0-%d): ' % (len(entries) - 1)))
            if entries[vid_num]:
                return entries[vid_num]
            elif vid_num == -1:
                return None

def get_video(url):
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url)

def play_next():
    log('Playing next track')
    set_state(STATE_NEXT)
    signal.alarm(0) # disable alarm

# Signals
def interrupted(signum, frame):
    log('Playback ended')
    play_next()
signal.signal(signal.SIGALRM, interrupted)

def signal_handler(sig, frame):
    menu_select()
signal.signal(signal.SIGINT, signal_handler)

while True:
    vid = None
    if is_state(STATE_PLAY):
        sleep(0.1)
        ret = get_state('player').poll()
        if not ret is None:
            play_next()
        continue
    elif is_state(STATE_FIND):
        search_term = input(search_text)
        if search_term == '-1':
            menu_select()
            continue
        vid = search(search_term)
    elif is_state(STATE_NEXT):
        vid = get_state('next_video')
        set_state(get_state() ^ STATE_NEXT)

    if vid is None:
        continue

    terminate_process(get_state('player'))
    set_state(play(vid, get_state('mpv_stdout'), get_state('mpv_stderr')), 'player')
    signal.alarm(max(int(vid.get('duration')) - 5, 0))

