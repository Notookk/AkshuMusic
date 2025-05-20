import asyncio
import logging
import os
import random
import re
import sys
import time
from typing import Optional, Tuple, Dict, List, Union
import aiohttp
from urllib.parse import quote

import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


class YouTubeAPI:
    def __init__(self):
        self.base_url = "https://www.youtube.com/watch?v="
        self.playlist_base = "https://youtube.com/playlist?list="
        self.url_regex = re.compile(
            r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
        )
        self.last_request = 0
        self.request_delay = 2.0
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Mobile/15E148 Safari/604.1"
        ]
        self.invidious_instances = [
            "https://yewtu.be",
            "https://inv.odyssey346.dev",
            "https://invidious.flokinet.to",
            "https://vid.puffyan.us",
            "https://inv.tux.pizza"
        ]
        self.current_instance_index = 0

    async def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        now = time.time()
        elapsed = now - self.last_request
        if elapsed < self.request_delay:
            await asyncio.sleep(self.request_delay - elapsed)
        self.last_request = time.time()
        self.request_delay = random.uniform(1.5, 2.5)

    def _get_ydl_opts(self, audio_only: bool = True, format_id: str = None) -> Dict:
        """Get options for yt-dlp based on download type."""
        opts = {
            'quiet': True,
            'no_warnings': True,
            'geo_bypass': True,
            'force_ipv4': True,
            'socket_timeout': 30,
            'retries': 3,
            'user_agent': random.choice(self.user_agents),
            'referer': 'https://www.youtube.com/',
            'noplaylist': True,
            'logger': logger,
            'extract_flat': False,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'ratelimit': 1048576,  # 1MB/s
        }

        if format_id:
            opts['format'] = format_id
        elif audio_only:
            opts['format'] = 'bestaudio/best'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]'

        return opts

    async def _get_from_invidious(self, query: str) -> Optional[Dict]:
        """Fallback to Invidious API if YouTube blocks the request."""
        for base in self.invidious_instances:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{base}/api/v1/search?q={quote(query)}"
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200:
                            results = await resp.json()
                            if results and isinstance(results, list):
                                video = next((v for v in results if v.get('type') == 'video'), None)
                                if video:
                                    return {
                                        'id': video.get('videoId'),
                                        'title': video.get('title', 'Unknown Title'),
                                        'duration': video.get('lengthSeconds', 0),
                                        'thumbnail': f"https://i.ytimg.com/vi/{video.get('videoId')}/hqdefault.jpg",
                                        'url': f"{self.base_url}{video.get('videoId')}"
                                    }
            except Exception as e:
                logger.warning(f"Invidious request failed on {base}: {str(e)}")
        return None

    async def url(self, message: Message) -> Optional[str]:
        """Extract YouTube URL from message or replied message."""
        try:
            messages_to_check = [message]
            if message.reply_to_message:
                messages_to_check.append(message.reply_to_message)
            
            for msg in messages_to_check:
                if not msg:
                    continue
                
                text = msg.text or msg.caption or ""
                entities = (msg.entities or []) + (msg.caption_entities or [])
                
                for entity in entities:
                    try:
                        if entity.type == MessageEntityType.URL:
                            url = text[entity.offset:entity.offset + entity.length]
                            if self.url_regex.match(url):
                                return url
                        elif entity.type == MessageEntityType.TEXT_LINK:
                            if self.url_regex.match(entity.url):
                                return entity.url
                    except Exception:
                        continue
            return None
        except Exception as e:
            logger.error(f"URL extraction failed: {str(e)}", exc_info=True)
            return None

    async def details(self, link: str, videoid: Union[bool, str] = None) -> Tuple[Optional[Dict], str]:
        """Get video details with multiple fallback methods."""
        try:
            await self._rate_limit()
            
            if videoid:
                link = self.base_url + link
            
            if "&" in link:
                link = link.split("&")[0]

            # First try with VideosSearch
            try:
                results = VideosSearch(link, limit=1)
                for result in (await results.next())["result"]:
                    duration_min = result["duration"]
                    return {
                        'title': result["title"],
                        'duration': duration_min,
                        'duration_sec': 0 if str(duration_min) == "None" else int(time_to_seconds(duration_min)),
                        'thumbnail': result["thumbnails"][0]["url"].split("?")[0],
                        'id': result["id"],
                        'url': result["link"]
                    }, ""
            except Exception as e:
                logger.warning(f"VideosSearch failed: {str(e)}")

            # Fallback to yt-dlp
            try:
                ydl_opts = self._get_ydl_opts()
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, link, download=False)
                    
                    if not info:
                        raise Exception("No info returned")
                    
                    if 'entries' in info:
                        if isinstance(info['entries'], list) and info['entries']:
                            info = info['entries'][0]
                        else:
                            raise Exception("No entries found")
                    
                    duration = info.get('duration', 0)
                    return {
                        'title': info.get('title', 'Unknown Title'),
                        'duration': duration,
                        'duration_sec': duration,
                        'thumbnail': info.get('thumbnail') or f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg",
                        'id': info['id'],
                        'url': f"{self.base_url}{info['id']}"
                    }, ""
            except yt_dlp.utils.ExtractorError as e:
                if "Sign in" in str(e):
                    fallback = await self._get_from_invidious(link)
                    if fallback:
                        return {
                            'title': fallback['title'],
                            'duration': fallback['duration'],
                            'duration_sec': fallback['duration'],
                            'thumbnail': fallback['thumbnail'],
                            'id': fallback['id'],
                            'url': fallback['url']
                        }, ""
                logger.error(f"Extractor error: {str(e)}")
                return None, f"Extraction failed: {str(e)}"
            except Exception as e:
                logger.error(f"Error getting details: {str(e)}")
                return None, f"Error: {str(e)}"

        except Exception as e:
            logger.error(f"Failed to get details: {str(e)}", exc_info=True)
            return None, f"Failed to process query: {str(e)}"

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        """Check if video exists."""
        if videoid:
            link = self.base_url + link
        return bool(re.search(self.url_regex, link))

    async def video(self, link: str, videoid: Union[bool, str] = None) -> Tuple[Optional[str], str]:
        """Get direct video stream URL."""
        try:
            await self._rate_limit()
            
            if videoid:
                link = self.base_url + link
            
            if "&" in link:
                link = link.split("&")[0]

            ydl_opts = self._get_ydl_opts(audio_only=False)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, link, download=False)
                if not info or not info.get('url'):
                    return None, "No stream URL available"
                return info['url'], ""
                
        except Exception as e:
            logger.error(f"Stream URL error: {str(e)}", exc_info=True)
            return None, f"Error getting stream URL: {str(e)}"

    async def playlist(self, link: str, limit: int, user_id: int, videoid: Union[bool, str] = None) -> List[str]:
        """Get playlist items."""
        try:
            await self._rate_limit()
            
            if videoid:
                link = self.playlist_base + link
            
            if "&" in link:
                link = link.split("&")[0]

            ydl_opts = {
                'extract_flat': True,
                'playlistend': limit,
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, link, download=False)
                if not info or not info.get('entries'):
                    return []
                
                return [entry['id'] for entry in info['entries'] if entry.get('id')]
                
        except Exception as e:
            logger.error(f"Playlist error: {str(e)}", exc_info=True)
            return []

    async def download(
        self,
        link: str,
        mystic=None,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> Union[str, Tuple[str, bool]]:
        """Download video or audio from YouTube."""
        try:
            await self._rate_limit()
            
            if videoid:
                link = self.base_url + link
            
            if "&" in link:
                link = link.split("&")[0]

            os.makedirs('downloads', exist_ok=True)
            
            if songvideo:
                ydl_opts = self._get_ydl_opts(audio_only=False, format_id=format_id)
                ydl_opts['outtmpl'] = f"downloads/{title}.mp4"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    await asyncio.to_thread(ydl.download, [link])
                return f"downloads/{title}.mp4"
            
            elif songaudio:
                ydl_opts = self._get_ydl_opts(audio_only=True, format_id=format_id)
                ydl_opts['outtmpl'] = f"downloads/{title}.mp3"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    await asyncio.to_thread(ydl.download, [link])
                return f"downloads/{title}.mp3"
            
            elif video:
                ydl_opts = self._get_ydl_opts(audio_only=False)
                ydl_opts['outtmpl'] = 'downloads/%(id)s.%(ext)s'
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, link, download=False)
                    path = ydl.prepare_filename(info)
                    if not os.path.exists(path):
                        await asyncio.to_thread(ydl.download, [link])
                return path, True
            
            else:  # Audio only
                ydl_opts = self._get_ydl_opts(audio_only=True)
                ydl_opts['outtmpl'] = 'downloads/%(id)s.%(ext)s'
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, link, download=False)
                    path = ydl.prepare_filename(info)
                    mp3_path = os.path.splitext(path)[0] + '.mp3'
                    if not os.path.exists(mp3_path):
                        await asyncio.to_thread(ydl.download, [link])
                        if os.path.exists(path) and not path.endswith('.mp3'):
                            os.rename(path, mp3_path)
                    return mp3_path, True
                
        except Exception as e:
            logger.error(f"Download error: {str(e)}", exc_info=True)
            raise Exception(f"Download failed: {str(e)}")

    # Other methods (title, duration, thumbnail, track, formats, slider) can be implemented
    # similarly using either VideosSearch or yt-dlp as fallback
