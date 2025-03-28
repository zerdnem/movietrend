from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.panel import Panel
import requests
import subprocess
from fuzzywuzzy import fuzz  # For string similarity
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Initialize Rich console
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

# Media player setting (default to MPV if not specified)
MEDIA_PLAYER = os.getenv("MEDIA_PLAYER", "mpv").lower()  # Can be "vlc", "mpv", etc.
SUPPORTED_PLAYERS = ["vlc", "mpv"]

# Validate API credentials
if not TRAKT_CLIENT_ID or not TRAKT_CLIENT_SECRET:
    console.print("[red]Error: TRAKT_CLIENT_ID or TRAKT_CLIENT_SECRET not found in .env file.[/red]")
    exit(1)

# Validate media player
if MEDIA_PLAYER not in SUPPORTED_PLAYERS:
    console.print(f"[red]Error: Unsupported media player '{MEDIA_PLAYER}'. Supported players are: {', '.join(SUPPORTED_PLAYERS)}.[/red]")
    console.print("[yellow]Defaulting to MPV.[/yellow]")
    MEDIA_PLAYER = "mpv"

def fetch_trending_content(media_type, period="trending"):
    """Fetch trending movies or TV shows from Trakt API."""
    try:
        url = f"{TRAKT_API_URL}/{media_type}/{period}"
        response = requests.get(url, headers=HEADERS, params={"limit": 10})
        response.raise_for_status()
        data = response.json()
        for item in data:
            item["media_type"] = media_type[:-1]  # e.g., "movies" -> "movie"
            item["title"] = item.get("movie", {}).get("title") if media_type == "movies" else item.get("show", {}).get("title")
        return data[:10]
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching {media_type} data: {e}[/red]")
        return []

def fetch_combined_trending():
    """Fetch and combine trending movies and TV shows."""
    movies = fetch_trending_content("movies", "trending")
    shows = fetch_trending_content("shows", "trending")
    
    # Debug: Print the number of movies and shows fetched
    console.print(f"[cyan]Fetched {len(movies)} trending movies and {len(shows)} trending shows.[/cyan]")
    
    # If movies are empty, inform the user
    if not movies:
        console.print("[yellow]No trending movies found. Displaying trending shows only.[/yellow]")
        shows.sort(key=lambda x: x.get("watchers", 0), reverse=True)
        return shows[:10]
    
    # If shows are empty, inform the user
    if not shows:
        console.print("[yellow]No trending shows found. Displaying trending movies only.[/yellow]")
        movies.sort(key=lambda x: x.get("watchers", 0), reverse=True)
        return movies[:10]
    
    # Take top 5 movies and top 5 shows to ensure both are represented
    movies.sort(key=lambda x: x.get("watchers", 0), reverse=True)
    shows.sort(key=lambda x: x.get("watchers", 0), reverse=True)
    combined = movies[:5] + shows[:5]
    return combined

def search_content(query):
    """Search for movies, TV shows, or anime by title using Trakt API."""
    try:
        url = f"{TRAKT_API_URL}/search/movie,show"
        params = {"query": query, "limit": 10}
        response = requests.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data:
            media_type = item["type"]  # "movie" or "show"
            if media_type == "movie":
                results.append({
                    "media_type": "movie",
                    "title": item["movie"]["title"],
                    "movie": item["movie"]
                })
            else:  # show
                results.append({
                    "media_type": "show",
                    "title": item["show"]["title"],
                    "show": item["show"]
                })
        return results[:10]
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error searching for content: {e}[/red]")
        return []

def display_content(content_list, title):
    """Display content in a table and return ranked list."""
    if not content_list:
        console.print(f"[yellow]No data available for {title}.[/yellow]")
        return []

    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Rank", style="cyan", justify="center")
    table.add_column("Type", style="blue")
    table.add_column("Name", style="green")

    ranked_list = []
    for rank, item in enumerate(content_list, start=1):
        media_type = item["media_type"]
        name = item.get("title", "N/A")
        table.add_row(str(rank), media_type.capitalize(), name)
        ranked_list.append((rank, name, item))

    console.print(table)
    return ranked_list

def fetch_movie_details(movie_id):
    """Fetch detailed information for a movie from Trakt API."""
    try:
        url = f"{TRAKT_API_URL}/movies/{movie_id}?extended=full"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching movie details: {e}[/red]")
        return None

def fetch_show_details(show_id):
    """Fetch detailed information for a show from Trakt API."""
    try:
        url = f"{TRAKT_API_URL}/shows/{show_id}?extended=full"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching show details: {e}[/red]")
        return None

def fetch_episode_details(show_id, season, episode):
    """Fetch details for a specific episode of a show."""
    try:
        url = f"{TRAKT_API_URL}/shows/{show_id}/seasons/{season}/episodes/{episode}?extended=full"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching episode details: {e}[/red]")
        return None

def display_details(item, season=None, episode=None):
    """Display detailed information about the movie or show."""
    media_type = item["media_type"]
    details = None
    episode_details = None

    if media_type == "movie":
        movie_id = item["movie"]["ids"]["trakt"]
        details = fetch_movie_details(movie_id)
    else:  # Show or anime
        show_id = item["show"]["ids"]["trakt"]
        details = fetch_show_details(show_id)
        if season and episode:
            episode_details = fetch_episode_details(show_id, season, episode)

    if not details:
        console.print("[yellow]Could not fetch details for this title.[/yellow]")
        return

    title = details.get("title", "N/A")
    overview = details.get("overview", "No overview available.")
    genres = ", ".join(details.get("genres", [])) or "N/A"
    rating = details.get("rating", "N/A")
    runtime = details.get("runtime", "N/A")
    year = details.get("year", "N/A")

    info_text = f"[bold cyan]Title:[/bold cyan] {title}\n"
    info_text += f"[bold cyan]Year:[/bold cyan] {year}\n"
    info_text += f"[bold cyan]Type:[/bold cyan] {media_type.capitalize()}\n"
    info_text += f"[bold cyan]Genres:[/bold cyan] {genres}\n"
    info_text += f"[bold cyan]Rating:[/bold cyan] {rating}/10\n"
    info_text += f"[bold cyan]Runtime:[/bold cyan] {runtime} minutes\n"
    info_text += f"[bold cyan]Overview:[/bold cyan] {overview}\n"

    if episode_details:
        ep_title = episode_details.get("title", "N/A")
        ep_number = episode_details.get("number", episode)
        ep_season = episode_details.get("season", season)
        ep_overview = episode_details.get("overview", "No overview available.")
        ep_air_date = episode_details.get("first_aired", "N/A")
        info_text += f"\n[bold cyan]Episode Details (S{ep_season:02d}E{ep_number:02d}):[/bold cyan]\n"
        info_text += f"[bold cyan]Episode Title:[/bold cyan] {ep_title}\n"
        info_text += f"[bold cyan]Air Date:[/bold cyan] {ep_air_date}\n"
        info_text += f"[bold cyan]Episode Overview:[/bold cyan] {ep_overview}\n"

    console.print(Panel(info_text, title=f"Details for {title}", border_style="green"))

def get_best_torrent(query, season=None, episode=None):
    """Fetch the best torrent from The Pirate Bay API with season/episode support."""
    if season and episode:
        query = f"{query} S{season:02d}E{episode:02d}"
    elif season:
        query = f"{query} S{season:02d}"
    
    url = f"https://apibay.org/q.php?q={query}"
    
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        torrent_list = response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching torrent data: {e}[/red]")
        return None

    if not torrent_list or torrent_list[0].get('id') == '0':
        console.print("[yellow]No torrents found for the query.[/yellow]")
        return None

    weights = {
        'name_similarity': 0.5,
        'quality': 0.3,
        'seeders': 0.2,
        'season_episode_match': 0.2
    }

    def get_quality_score(title):
        if '4k' in title.lower() or '2160p' in title.lower():
            return 1.0
        elif '1080p' in title.lower():
            return 0.8
        elif '720p' in title.lower():
            return 0.6
        return 0.3

    def get_seeders_score(seeders, max_seeders):
        return min(seeders / max_seeders, 1.0) if max_seeders > 0 else 0

    def get_season_episode_score(title, season, episode):
        if not season or not episode:
            return 0.0
        season_episode_pattern = rf"S{season:02d}E{episode:02d}"
        if season_episode_pattern.lower() in title.lower():
            return 1.0
        season_pattern = rf"S{season:02d}"
        if season_pattern.lower() in title.lower():
            return 0.5
        return 0.0

    max_seeders = max([int(t.get('seeders', 0)) for t in torrent_list], default=1)
    scored_torrents = []
    for torrent in torrent_list:
        title = torrent.get('name', '').lower()
        name_similarity = fuzz.token_sort_ratio(query.lower(), title) / 100.0
        quality_score = get_quality_score(title)
        seeders = int(torrent.get('seeders', 0))
        seeders_score = get_seeders_score(seeders, max_seeders)
        season_episode_score = get_season_episode_score(title, season, episode)
        
        total_score = (weights['name_similarity'] * name_similarity +
                       weights['quality'] * quality_score +
                       weights['seeders'] * seeders_score +
                       weights['season_episode_match'] * season_episode_score)
        scored_torrents.append({'torrent': torrent, 'score': total_score})

    if scored_torrents:
        best_match = max(scored_torrents, key=lambda x: x['score'])
        return best_match['torrent']
    return None

def play_with_peerflix(title, media_type, item):
    """Stream the selected title using peerflix with season/episode handling and details display."""
    season = None
    episode = None
    if media_type == "show":
        console.print("[yellow]Since this is a TV show or anime, please specify the season and episode.[/yellow]")
        season = Prompt.ask("[bold yellow]Enter the season number (e.g., 1)[/bold yellow]", default="1")
        episode = Prompt.ask("[bold yellow]Enter the episode number (e.g., 1)[/bold yellow]", default="1")
        try:
            season = int(season)
            episode = int(episode)
        except ValueError:
            console.print("[red]Invalid season or episode number. Using general search instead.[/red]")
            season = None
            episode = None

    # Display details before proceeding
    display_details(item, season, episode)

    # Confirm with the user
    confirm = Prompt.ask(
        "[bold yellow]Do you want to stream this title? (y/n)[/bold yellow]",
        choices=["y", "n"],
        default="y"
    )
    if confirm != "y":
        console.print("[green]Streaming cancelled.[/green]")
        return

    # Proceed with torrent search and streaming
    console.print(f"[bold cyan]Searching for torrent for '{title}'...[/bold cyan]")
    best_torrent = get_best_torrent(title, season, episode)
    
    if best_torrent:
        magnet = f"magnet:?xt=urn:btih:{best_torrent['info_hash']}&dn={best_torrent['name']}"
        console.print(f"[green]Found torrent: {best_torrent['name']} (Seeders: {best_torrent['seeders']})[/green]")
        console.print(f"[yellow]Streaming with peerflix using {MEDIA_PLAYER.upper()}...[/yellow]")
        console.print(f"[yellow]Note: Ensure peerflix and {MEDIA_PLAYER.upper()} are installed.[/yellow]")
        
        try:
            subprocess.run(["peerflix", magnet, f"--{MEDIA_PLAYER}"], check=True)
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error running peerflix: {e}[/red]")
        except FileNotFoundError:
            console.print(f"[red]peerflix not found. Install it via npm: 'npm i -g peerflix'.[/red]")
            console.print(f"[red]Also ensure {MEDIA_PLAYER.upper()} is installed and accessible in your PATH.[/red]")
    else:
        console.print(f"[red]No suitable torrent found for '{title}'.[/red]")

def main_menu():
    """Display the main menu and handle user input."""
    console.print("[bold yellow]Note:[/bold yellow] 'Trending Shows' displays the top 10 trending movies and TV shows for today.")
    console.print("[bold yellow]Legal Warning:[/bold yellow] Streaming torrents may involve copyrighted material. Use legal alternatives like Tubi or Pluto TV to support creators.")

    while True:
        console.print("\n[bold cyan]=== Movies & TV Shows Streaming Menu ===[/bold cyan]")
        console.print("1. Trending Shows")
        console.print("2. Search for a Title")
        console.print("3. Exit")

        choice = Prompt.ask("[bold yellow]Select an option (1-3)[/bold yellow]", choices=["1", "2", "3"], default="1")

        ranked_list = []
        if choice == "1":
            content_list = fetch_combined_trending()
            ranked_list = display_content(content_list, "Top 10 Trending Movies & TV Shows")
        elif choice == "2":
            search_query = Prompt.ask("[bold yellow]Enter the title to search for (e.g., The Matrix, Naruto)[/bold yellow]")
            content_list = search_content(search_query)
            ranked_list = display_content(content_list, f"Search Results for '{search_query}'")
        elif choice == "3":
            console.print("[green]Goodbye![/green]")
            break

        if ranked_list:
            selection = Prompt.ask(
                f"[bold yellow]Enter the rank of the title to play (1-{len(ranked_list)}) or 0 to go back[/bold yellow]",
                choices=[str(i) for i in range(len(ranked_list) + 1)],
                default="0"
            )
            if selection != "0":
                selected_rank = int(selection)
                selected_item = next((item for item in ranked_list if item[0] == selected_rank), None)
                if selected_item:
                    title = selected_item[1]
                    media_type = selected_item[2]["media_type"]
                    play_with_peerflix(title, media_type, selected_item[2])

if __name__ == "__main__":
    console.print("[bold green]Welcome to the Movies & TV Shows Streaming App (Powered by Trakt and Peerflix)![/bold green]")
    main_menu()
