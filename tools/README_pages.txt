DEVICE PAGES FOR SEARCH ENGINES
===============================
What it does
  Every Monday (05:30 UTC) GitHub reads the three published sheets and writes one simple
  page per device, for example
      https://whatwatts.github.io/d/chargers/anker-737-120w/
  with the key measurements, photo, verdict, review and buy links. It also builds ranking pages
  in the folder best/, for example https://whatwatts.github.io/best/chargers-efficiency/
  (most efficient chargers, lowest ripple, lowest cable resistance, highest real power bank
  capacity and so on), a hub page https://whatwatts.github.io/best/ and rewrites
  sitemap.xml (home page + device pages + rankings).
  Every ranking is sorted by one measured value, there are no invented scores. A ranking is
  published only when at least 3 devices have the needed data; at most 30 are listed.
  Nothing else is touched: index.html, media/ and the main site work exactly as before.
  A visitor who lands on such a page sees the numbers and can press "Open in the full database"
  to go to the same device in the big table.

First run
  1. Upload tools/build_pages.py and .github/workflows/build-pages.yml (keep the folders).
  2. Actions -> Build device pages -> Run workflow. In a minute the folder "d" appears.
  3. Open one page by hand (the address above, with a real device name) and the page /best/ and check them.
  4. Google Search Console -> Sitemaps -> add  sitemap.xml  (once). Indexing takes weeks.

When the table changes
  Pages are rebuilt on the next Monday run, or at once by Run workflow.
  A device removed from the table loses its page; a new device gets one.
  If a sheet cannot be downloaded, its old pages are kept.

Address of a page = /d/<cables|chargers|powerbanks>/<name of the device in small letters, other characters replaced by "-">/
