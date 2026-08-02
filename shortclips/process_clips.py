#!/usr/bin/env python3
"""
process_clips.py — Short Clips processor for GitHub Actions.

Flow:
  1. Reads pending clips for a specific video from the Cortex GAS sheet
  2. Downloads the source video from Google Drive using OAuth2
  3. Trims each clip with ffmpeg
  4. Uploads each clip back to Google Drive (output folder)
  5. Updates the clips sheet with Drive links
  6. Updates the videos sheet with final status and clip count

Environment variables (set as GitHub Secrets):
  GAS_URL                — Cortex GAS web app URL
  API_KEY                — Cortex GAS API key
  GOOGLE_CLIENT_ID       — OAuth2 client ID
  GOOGLE_CLIENT_SECRET   — OAuth2 client secret
  GOOGLE_REFRESH_TOKEN   — OAuth2 refresh token
  CLIP_OUTPUT_FOLDER_ID  — Drive folder ID for generated clips

Workflow inputs (passed via github.event.inputs):
  VIDEO_FILE_ID          — Drive file ID of the source video
  VIDEO_ROW_INDEX        — Row index in the videos sheet
"""

import os
import re
import subprocess
import sys
import json
import tempfile
from collections import defaultdict
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import io


# ── Config ───────────────────────────────────────────────────────────────────

GAS_URL            = os.environ.get('GAS_URL', '')
API_KEY            = os.environ.get('API_KEY', '')
OUTPUT_FOLDER_ID   = os.environ.get('CLIP_OUTPUT_FOLDER_ID', '')
VIDEO_FILE_ID      = os.environ.get('VIDEO_FILE_ID', '')
VIDEO_ROW_INDEX    = int(os.environ.get('VIDEO_ROW_INDEX', '0') or '0')

OUTPUT_DIR = Path('/tmp/clips_output')


# ── Google credentials ───────────────────────────────────────────────────────

def get_drive_creds():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get('GOOGLE_REFRESH_TOKEN', ''),
        client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        token_uri='https://oauth2.googleapis.com/token',
        scopes=['https://www.googleapis.com/auth/drive']
    )
    creds.refresh(GoogleRequest())
    return creds


# ── GAS API helpers ──────────────────────────────────────────────────────────

def gas_post(action, params=None):
    payload = {'action': action, 'apiKey': API_KEY}
    if params:
        payload.update(params)
    r = requests.post(GAS_URL, json=payload,
                      headers={'Content-Type': 'text/plain'}, timeout=30)
    r.raise_for_status()
    return r.json()


def get_pending_clips_for_video(video_file_id):
    result = gas_post('listClips')
    if not result.get('success'):
        raise RuntimeError(result.get('error', 'listClips failed'))
    return [
        c for c in result.get('clips', [])
        if c.get('videoFileId', '') == video_file_id and c.get('status') == 'pending'
    ]


def mark_clip_generating(row_index):
    gas_post('updateClipStatus', {'rowIndex': row_index, 'status': 'generating'})


def mark_clip_ready(row_index, drive_link):
    gas_post('updateClipStatus', {
        'rowIndex': row_index, 'status': 'ready', 'driveLink': drive_link
    })


def mark_clip_error(row_index, error_msg):
    gas_post('updateClipStatus', {
        'rowIndex': row_index, 'status': 'error', 'errorMsg': error_msg[:500]
    })


def update_video_status(row_index, status, clip_count=None, error_msg=''):
    payload = {
        'rowIndex': row_index,
        'status':   status,
        'errorMsg': error_msg,
    }
    if clip_count is not None:
        payload['clipCount'] = clip_count
    gas_post('updateVideoStatus', payload)


# ── Timestamp helpers ────────────────────────────────────────────────────────

def normalize_timestamp(ts):
    """Handle Google Sheets auto-converted timestamps."""
    ts = str(ts).strip()
    if not ts or ts == 'None':
        return '0:00'
    if 'GMT' in ts or '1899' in ts or 'Standard Time' in ts:
        parts = ts.split()
        for part in parts:
            if re.match(r'^\d{1,3}:\d{2}:\d{2}$', part):
                h, m, s = map(int, part.split(':'))
                if h > 12 and s == 0:
                    return str(h) + ':' + (str(m).zfill(2))
                return part
    if re.match(r'^\d{1,3}:\d{2}:\d{2}$', ts):
        h, m, s = map(int, ts.split(':'))
        if h > 12 and s == 0:
            return str(h) + ':' + (str(m) if m >= 10 else '0' + str(m))
    return ts


def ts_to_secs(ts):
    ts = normalize_timestamp(ts).strip()
    parts = ts.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


# ── Drive helpers ────────────────────────────────────────────────────────────

def download_from_drive(file_id, dest_path):
    """Download a file from Google Drive to dest_path."""
    creds   = get_drive_creds()
    service = build('drive', 'v3', credentials=creds)
    request = service.files().get_media(fileId=file_id)
    fh      = io.FileIO(dest_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request, chunksize=50 * 1024 * 1024)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            print(f'  Download {int(status.progress() * 100)}%')
    fh.close()
    print(f'  Downloaded: {dest_path} ({os.path.getsize(dest_path) // (1024*1024)} MB)')


def upload_to_drive(file_path, file_name, folder_id):
    """Upload a file to Google Drive output folder. Returns web view link."""
    creds    = get_drive_creds()
    service  = build('drive', 'v3', credentials=creds)
    metadata = {'name': file_name, 'parents': [folder_id]}
    media    = MediaFileUpload(str(file_path), mimetype='video/mp4', resumable=True)
    uploaded = service.files().create(
        body=metadata, media_body=media, fields='id,webViewLink'
    ).execute()
    file_id = uploaded.get('id', '')
    # Make readable by anyone with the link
    service.permissions().create(
        fileId=file_id, body={'type': 'anyone', 'role': 'reader'}
    ).execute()
    return uploaded.get('webViewLink', '')


# ── Video processing ─────────────────────────────────────────────────────────

def trim_video(input_path, start, end, output_path):
    start_secs    = ts_to_secs(start)
    end_secs      = ts_to_secs(end)
    duration_secs = max(1.0, end_secs - start_secs)

    print(f'  Trimming {start} → {end}  ({duration_secs:.1f}s)')

    cmd = [
        'ffmpeg',
        '-ss', str(start_secs),
        '-i', str(input_path),
        '-t', str(duration_secs),
        '-c:v', 'libx264',
        '-c:a', 'aac',
        '-preset', 'fast',
        '-crf', '23',
        '-avoid_negative_ts', 'make_zero',
        '-y', str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f'ffmpeg failed: {result.stderr[-800:]}')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not GAS_URL or not API_KEY:
        print('ERROR: GAS_URL and API_KEY must be set.')
        sys.exit(1)
    if not VIDEO_FILE_ID:
        print('ERROR: VIDEO_FILE_ID must be set.')
        sys.exit(1)
    if not OUTPUT_FOLDER_ID:
        print('ERROR: CLIP_OUTPUT_FOLDER_ID must be set.')
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f'Processing video: {VIDEO_FILE_ID}')
    print('Fetching pending clips from sheet...')

    clips = get_pending_clips_for_video(VIDEO_FILE_ID)
    if not clips:
        print('No pending clips found for this video.')
        if VIDEO_ROW_INDEX >= 2:
            update_video_status(VIDEO_ROW_INDEX, 'ready', 0)
        return

    print(f'Found {len(clips)} pending clip(s).\n')

    # Download source video once
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, 'source.mp4')
        print(f'Downloading source video from Drive...')
        try:
            download_from_drive(VIDEO_FILE_ID, video_path)
        except Exception as exc:
            error = f'Download failed: {exc}'
            print(f'ERROR: {error}')
            for clip in clips:
                mark_clip_error(clip['rowIndex'], error)
            if VIDEO_ROW_INDEX >= 2:
                update_video_status(VIDEO_ROW_INDEX, 'error', 0, error)
            return

        # Trim each clip
        success_count = 0
        for clip in clips:
            row_index  = clip['rowIndex']
            clip_title = clip.get('clipTitle', 'clip').strip()
            start      = normalize_timestamp(clip.get('start', ''))
            end        = normalize_timestamp(clip.get('end', ''))

            safe_title   = re.sub(r'[^\w\-\s]', '', clip_title)[:50].strip() or 'clip'
            out_filename = safe_title + '.mp4'
            out_path     = OUTPUT_DIR / out_filename

            print(f'[{row_index}] {clip_title}  ({start} → {end})')

            try:
                mark_clip_generating(row_index)
                trim_video(video_path, start, end, out_path)

                print(f'  Uploading to Drive...')
                drive_link = upload_to_drive(out_path, out_filename, OUTPUT_FOLDER_ID)
                mark_clip_ready(row_index, drive_link)
                print(f'  Ready: {drive_link}')
                success_count += 1

            except Exception as exc:
                print(f'  ERROR: {exc}')
                mark_clip_error(row_index, str(exc))

    # Update video row
    if VIDEO_ROW_INDEX >= 2:
        final_status = 'ready' if success_count > 0 else 'error'
        update_video_status(VIDEO_ROW_INDEX, final_status, success_count)

    print(f'\nDone. {success_count}/{len(clips)} clips generated.')


if __name__ == '__main__':
    main()
