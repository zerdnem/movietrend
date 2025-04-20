from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
import inquirer
import requests
import subprocess
import os
from dotenv import load_dotenv
from datetime import datetime
import re
import uuid

load_dotenv()
console = Console()

# Trakt API settings
TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.getenv("TRAKT_CLIENT_SECRET")
TRAKT_API_URL = "https://api.trakt.tv"
HEADERS = {
    "Content-Type": "application/json",
    "trakt-api-version": "2",
    "trakt-api-key": TRAKT_CLIENT_ID
}

MEDIA_PLAYER = os.getenv("MEDIA_PLAYER", "mpv").lower()
SUPPORTED_PLAYERS = ["vlc", "mpv"]
MIN_SEEDERS_THRESHOLD = 5
YTS_API_URL = "https://yts.mx/api/v2/list_movies.json"

if not TRAKT_CLIENT_ID or not TRAKT_CLIENT_SECRET:
    console.print("[red]Error: TRAKT_CLIENT_ID or TRAKT_CLIENT_SECRET missing.[/red]")
    exit(1)
if MEDIA_PLAYER not in SUPPORTED_PLAYERS:
    console.print(f"[red]Unsupported player '{MEDIA_PLAYER}'. Defaulting to MPV.[/red]")
    MEDIA_PLAYER = "mpv"

# Fetch seasons for a show
def fetch_show_seasons(show_id):
    """Fetch seasons for a given show from Trakt API."""
    try:
        url = f"{TRAKT_API_URL}/shows/{show_id}/seasons"
        response = requests.get(url, headers=HEADERS, params={"extended": "full"}, timeout=10)
        response.raise_for_status()
        seasons = response.json()
        return seasons
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching seasons for show {show_id}: {e}[/red]")
        return []

# Fetch episodes for a season
def fetch_season_episodes(show_id, season_number):
    """Fetch episodes for a specific season of a show."""
    try:
        url = f"{TRAKT_API_URL}/shows/{show_id}/seasons/{season_number}"
        response = requests.get(url, headers=HEADERS, params={"extended": "full"}, timeout=10)
        response.raise_for_status()
        episodes = response.json()
        return episodes
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching episodes for season {season_number}: {e}[/red]")
        return []

# Fetch trending content with season info for shows
def fetch_trending_content(media_type):
    """Fetch trending movies or shows from Trakt API, including season info for shows."""
    try:
        url = f"{TRAKT_API_URL}/movies/trending" if media_type == "movies" else f"{TRAKT_API_URL}/shows/trending"
        response = requests.get(url, headers=HEADERS, params={"limit": 10}, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data:
            content = item.get("movie") if media_type == "movies" else item.get("show")
            if not content:
                continue
            trending_season = None
            if media_type == "shows":
                seasons = fetch_show_seasons(content["ids"]["trakt"])
                if seasons:
                    valid_seasons = [s for s in seasons if s.get("first_aired") and s.get("episode_count", 0) > 0]
                    if valid_seasons:
                        trending_season = max(valid_seasons, key=lambda x: x.get("first_aired", ""))
                        trending_season = trending_season["number"]
            results.append({
                "media_type": media_type[:-1],
                "title": content["title"],
                "watchers": item.get("watchers", 0),
                "trending_season": trending_season if media_type == "shows" else None,
                media_type[:-1]: content
            })
        return results
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching trending {media_type}: {e}[/red]")
        return []

# Main torrent provider (apibay.org) - Returns up to 5 torrents
def get_torrents(query, season=None, episode=None):
    if season and episode:
        query = f"{query} S{season:02d}E{episode:02d}"
    elif season:
        query = f"{query} S{season:02d}"
    url = f"https://apibay.org/q.php?q={query}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        torrents = response.json()
        if not torrents or torrents[0].get("id") == "0":
            return []
        # Filter and sort torrents by seeders, limit to 5
        viable_torrents = [
            {
                "magnet": f"magnet:?xt=urn:btih:{t['info_hash']}&dn={t['name']}",
                "source": "Apibay",
                "seeders": int(t.get("seeders", 0)),
                "name": t['name']
            }
            for t in torrents if int(t.get("seeders", 0)) >= MIN_SEEDERS_THRESHOLD
        ]
        viable_torrents.sort(key=lambda x: x["seeders"], reverse=True)
        return viable_torrents[:5]
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]Apibay torrent search failed: {e}[/yellow]")
        return []

# YTS torrent provider (fallback for movies) - Returns up to 5 torrents
def get_yts_torrents(query):
    """Search YTS API for movie torrents and return up to 5 viable ones."""
    try:
        response = requests.get(YTS_API_URL, params={"query_term": query, "limit": 10}, timeout=10)
        response.raise_for_status()
        data = response.json()
        movies = data.get("data", {}).get("movies", [])
        if not movies:
            console.print(f"[yellow]No torrents found for '{query}' on YTS.[/yellow]")
            return []

        torrents = []
        for movie in movies:
            for torrent in movie.get("torrents", []):
                seeders = torrent.get("seeds", 0)
                if seeders >= MIN_SEEDERS_THRESHOLD:
                    torrents.append({
                        "magnet": torrent.get("url"),
                        "source": "YTS",
                        "seeders": seeders,
                        "name": f"{movie['title']} ({torrent.get('quality')})"
                    })
        
        torrents.sort(key=lambda x: x["seeders"], reverse=True)
        if torrents:
            console.print(f"[green]Found {len(torrents)} viable torrents on YTS.[/green]")
            return torrents[:5]
        else:
            console.print(f"[yellow]No viable torrents found for '{query}' on YTS (Seeders < {MIN_SEEDERS_THRESHOLD}).[/yellow]")
            return []
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]YTS torrent search failed: {e}[/yellow]")
        return []

# Display content with season info
def display_content(content_list, title):
    if not content_list:
        console.print(f"[yellow]No data for {title}.[/yellow]")
        return []
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Rank", style="cyan", justify="center")
    table.add_column("Type", style="blue")
    table.add_column("Name", style="green")
    table.add_column("Trending Season", style="yellow", justify="center")
    ranked_list = []
    for rank, item in enumerate(content_list, 1):
        season_display = str(item["trending_season"]) if item.get("trending_season") else "-"
        table.add_row(str(rank), item["media_type"].capitalize(), item["title"], season_display)
        ranked_list.append((rank, item["title"], item))
    console.print(table)
    return ranked_list

# Play content with torrent streaming and YTS fallback for movies
def play_content(title, media_type, item):
    if media_type == "movie":
        # Try main provider (Apibay)
        console.print(f"[cyan]Searching '{title}' on Apibay torrents...[/cyan]")
        torrents = get_torrents(title)
        if torrents:
            console.print(f"[green]Found {len(torrents)} viable torrents on Apibay.[/green]")
            torrent_choices = [
                (f"{t['name']} (Seeders: {t['seeders']}, Source: {t['source']})", t)
                for t in torrents
            ]
            torrent_choices.append(("None (Try YTS)", None))
            questions = [
                inquirer.List('torrent',
                              message=f"Select a torrent for '{title}'",
                              choices=torrent_choices,
                              default=torrent_choices[0][1])
            ]
            selected_torrent = inquirer.prompt(questions)['torrent']
            
            if selected_torrent:
                try:
                    subprocess.run(["peerflix", selected_torrent["magnet"], f"--{MEDIA_PLAYER}"], check=True)
                    return
                except FileNotFoundError:
                    console.print("[red]Peerflix not installed. Run 'npm i -g peerflix'.[/red]")
                except subprocess.CalledProcessError as e:
                    console.print(f"[red]Error streaming torrent: {e}[/red]")
                    return
        
        # Fallback to YTS
        console.print(f"[cyan]No viable torrents on Apibay. Trying YTS...[/cyan]")
        torrents = get_yts_torrents(title)
        if torrents:
            console.print(f"[green]Found {len(torrents)} viable torrents on YTS.[/green]")
            torrent_choices = [
                (f"{t['name']} (Seeders: {t['seeders']}, Source: {t['source']})", t)
                for t in torrents
            ]
            torrent_choices.append(("None", None))
            questions = [
                inquirer.List('torrent',
                              message=f"Select a torrent for '{title}'",
                              choices=torrent_choices,
                              default=torrent_choices[0][1])
            ]
            selected_torrent = inquirer.prompt(questions)['torrent']
            
            if selected_torrent:
                try:
                    subprocess.run(["peerflix", selected_torrent["magnet"], f"--{MEDIA_PLAYER}"], check=True)
                    return
                except FileNotFoundError:
                    console.print("[red]Peerflix not installed. Run 'npm i -g peerflix'.[/red]")
                except subprocess.CalledProcessError as e:
                    console.print(f"[red]Error streaming torrent: {e}[/red]")
                    return
        console.print(f"[red]No viable torrents found for '{title}'.[/red]")
        return

    # Handle shows with trending season as default (Apibay only)
    show_id = item["show"]["ids"]["trakt"]
    trending_season = item.get("trending_season", 1)
    
    while True:
        # Fetch seasons
        seasons = fetch_show_seasons(show_id)
        valid_seasons = [s for s in seasons if s.get("episode_count", 0) > 0]
        if not valid_seasons:
            console.print("[red]No valid seasons found.[/red]")
            return
        
        season_choices = [
            (f"Season {s['number']}", s['number'])
            for s in valid_seasons
        ]
        questions = [
            inquirer.List('season',
                         message="Select a season",
                         choices=season_choices,
                         default=[c[1] for c in season_choices if c[1] == trending_season][0])
        ]
        season = inquirer.prompt(questions)['season']
        
        # Fetch episodes for selected season
        episodes = fetch_season_episodes(show_id, season)
        if not episodes:
            console.print(f"[red]No episodes found for Season {season}.[/red]")
            retry = Prompt.ask(
                "[bold yellow]Try another season? (y/n)[/bold yellow]",
                choices=["y", "n"],
                default="y"
            )
            if retry == "y":
                continue
            return
        
        episode_choices = [
            (f"Episode {e['number']}", e['number'])
            for e in episodes
        ]
        questions = [
            inquirer.List('episode',
                         message="Select an episode",
                         choices=episode_choices,
                         default=episode_choices[0][1])
        ]
        episode = inquirer.prompt(questions)['episode']

        while True:
            console.print(f"[cyan]Playing '{title}' S{season:02d}E{episode:02d}...[/cyan]")
            console.print(f"[cyan]Searching '{title}' S{season:02d}E{episode:02d} on Apibay torrents..")
            torrents = get_torrents(title, season, episode)
            
            if torrents:
                console.print(f"[green]Found {len(torrents)} viable torrents on Apibay.[/green]")
                torrent_choices = [
                    (f"{t['name']} (Seeders: {t['seeders']}, Source: {t['source']})", t)
                    for t in torrents
                ]
                torrent_choices.append(("None", None))
                questions = [
                    inquirer.List('torrent',
                                  message=f"Select a torrent for '{title}' S{season:02d}E{episode:02d}",
                                  choices=torrent_choices,
                                  default=torrent_choices[0][1])
                ]
                selected_torrent = inquirer.prompt(questions)['torrent']
                
                if selected_torrent:
                    try:
                        subprocess.run(["peerflix", selected_torrent["magnet"], f"--{MEDIA_PLAYER}"], check=True)
                    except FileNotFoundError:
                        console.print("[red]Peerflix not installed. Run 'npm i -g peerflix'.[/red]")
                    except subprocess.CalledProcessError as e:
                        console.print(f"[red]Error streaming torrent: {e}[/red]")
                    else:
                        # Torrent played successfully, check for next episode
                        next_episode = fetch_next_episode(show_id, season, episode)
                        if next_episode:
                            season, episode = next_episode
                            continue_play = Prompt.ask(
                                f"[bold yellow]Play next episode (S{season:02d}E{episode:02d})? (y/n)[/bold yellow]",
                                choices=["y", "n"],
                                default="y"
                            )
                            if continue_play == "y":
                                continue
                        console.print("[green]No more episodes available.[/green]")
                        return
            else:
                console.print(f"[red]No viable torrents found for '{title}' S{season:02d}E{episode:02d} (Seeders < {MIN_SEEDERS_THRESHOLD}).[/red]")
            
            # No stream found, ask if user wants to try next episode
            next_episode = fetch_next_episode(show_id, season, episode)
            if next_episode:
                season, episode = next_episode
                continue_play = Prompt.ask(
                    f"[bold yellow]No torrent found. Try next episode (S{season:02d}E{episode:02d})? (y/n)[/bold yellow]",
                    choices=["y", "n"],
                    default="n"
                )
                if continue_play == "y":
                    continue
            console.print("[red]No more episodes or torrents available.[/red]")
            break
        break

# Fetch next episode
def fetch_next_episode(show_id, current_season, current_episode):
    """Determine the next episode to play."""
    episodes = fetch_season_episodes(show_id, current_season)
    if not episodes:
        return None
    
    for ep in episodes:
        if ep["number"] == current_episode + 1:
            return (current_season, current_episode + 1)
    
    seasons = fetch_show_seasons(show_id)
    for season in seasons:
        if season["number"] == current_season + 1 and season.get("episode_count", 0) > 0:
            return (current_season + 1, 1)
    
    return None

# Fetch and display content
def fetch_combined_trending():
    movies = fetch_trending_content("movies")
    shows = fetch_trending_content("shows")
    combined = (movies[:5] + shows[:5]) if movies and shows else movies or shows
    combined.sort(key=lambda x: x.get("watchers", 0), reverse=True)
    return combined[:10]

def search_content(query):
    try:
        url = f"{TRAKT_API_URL}/search/movie,show"
        response = requests.get(url, headers=HEADERS, params={"query": query, "limit": 10})
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data:
            media_type = item["type"]
            trending_season = None
            if media_type == "show":
                seasons = fetch_show_seasons(item["show"]["ids"]["trakt"])
                if seasons:
                    valid_seasons = [s for s in seasons if s.get("first_aired") and s.get("episode_count", 0) > 0]
                    if valid_seasons:
                        trending_season = max(valid_seasons, key=lambda x: x.get("first_aired", ""))
                        trending_season = trending_season["number"]
            results.append({
                "media_type": media_type,
                "title": item[media_type]["title"],
                "trending_season": trending_season,
                media_type: item[media_type]
            })
        return results[:10]
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error searching content: {e}[/red]")
        return []

# Main menu
def main_menu():
    console.print("[bold green]CLI Streaming App with Torrents[/bold green]")
    console.print(f"[yellow]Note: Torrents with <{MIN_SEEDERS_THRESHOLD} seeders are considered non-viable. YTS is used as a fallback for movies.[/yellow]")
    
    while True:
        console.print("\n[bold cyan]=== Menu ===[/bold cyan]")
        console.print("1. Trending Content")
        console.print("2. Search Title")
        console.print("3. Exit")
        choice = Prompt.ask("[bold yellow]Choose (1-3)[/bold yellow]", choices=["1", "2", "3"], default="1")

        ranked_list = []
        if choice == "1":
            ranked_list = display_content(fetch_combined_trending(), "Trending Content")
        elif choice == "2":
            query = Prompt.ask("[bold yellow]Enter title[/bold yellow]")
            ranked_list = display_content(search_content(query), f"Results for '{query}'")
        elif choice == "3":
            console.print("[green]Exiting...[/green]")
            break

        if ranked_list:
            choices = [str(i) for i in range(len(ranked_list) + 1)]
            selection = Prompt.ask(
                f"[bold yellow]Choose rank (1-{len(ranked_list)}) or 0 to go back[/bold yellow]",
                choices=choices,
                default="0"
            )
            if selection != "0":
                selected = next(item for item in ranked_list if item[0] == int(selection))
                play_content(selected[1], selected[2]["media_type"], selected[2])

if __name__ == "__main__":
    main_menu()
