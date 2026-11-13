"""Generates an offline RSS fixture with fresh dates so the pipeline can be tested without network."""
import datetime as dt, email.utils, pathlib
now = dt.datetime.now(dt.timezone.utc)
def d(h): return email.utils.format_datetime(now - dt.timedelta(hours=h))
items = [
 ("Zermatt opens new 10-person gondola to Klein Matterhorn", "The new gondola can carry 3,000 passengers per hour. Construction cost CHF 120 million. Skiers can use it from December 1.", 5),
 ("Zermatt unveils new gondola to Klein Matterhorn this winter", "Operators say the new lift replaces an older cable car.", 6),
 ("Skier killed in avalanche near resort", "A skier died on Saturday.", 3),
 ("Best ski jackets deals this week", "Save 40% on jackets.", 2),
 ("Local council debates parking rules", "Nothing about skiing here, just parking.", 4),
 ("Old story about snow guns", "Resort adds snow guns and snowmaking upgrades.", 400),
]
rows = "".join(f"<item><title>{t}</title><link>https://example.com/a{i}</link><description>{s}</description><pubDate>{d(h)}</pubDate></item>" for i,(t,s,h) in enumerate(items))
xml = f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{rows}</channel></rss>'
p = pathlib.Path(__file__).parent / "fixtures" / "feed.xml"
p.write_text(xml, encoding="utf-8"); print(p)
