import asyncio
import json
import time
import uuid
from collections import namedtuple
from PIL import Image
from io import BytesIO

import aiofiles
import aiohttp
import feedparser
import lxml.html
from jinja2 import Environment, FileSystemLoader
from lxml.html.clean import Cleaner
from tenacity import retry, stop_after_attempt, wait_random

Article = namedtuple("Article", "id, title, description, image_list")
Section = namedtuple("Section", "title, article_list")
Magazine = namedtuple("Magazine", "id, title, date, section_list")


async def create_Magazine(setting):
    title = setting.get("title", "Rss")
    feed_list = setting["feed_list"]

    section_list = await async_map(create_section, feed_list)
    if not section_list:
        return None

    magazine = Magazine(
        uuid.uuid4().hex, title, time.strftime("%Y-%m-%d"), section_list
    )
    await write_magazine(magazine)

    return magazine


async def write_magazine(magazine):
    await asyncio.gather(
        write_content(magazine),
        write_toc_html(magazine),
        write_toc_ncx(magazine)
    )


async def write_content(magazine):
    env = Environment(loader=FileSystemLoader("templates"), enable_async=True)
    template = env.get_template("content.opf")
    content = await template.render_async(magazine=magazine)
    async with aiofiles.open(f"content/content.opf", "w") as f:
        await f.write(content)


async def write_toc_html(magazine):
    env = Environment(loader=FileSystemLoader("templates"), enable_async=True)
    template = env.get_template("toc.html")
    toc_html = await template.render_async(magazine=magazine)
    async with aiofiles.open(f"content/toc.html", "w") as f:
        await f.write(toc_html)


async def write_toc_ncx(magazine):
    env = Environment(loader=FileSystemLoader("templates"), enable_async=True)
    template = env.get_template("toc.ncx")
    toc_ncx = await template.render_async(magazine=magazine)
    async with aiofiles.open(f"content/toc.ncx", "w") as f:
        await f.write(toc_ncx)


async def create_section(feed):
    title = feed["title"]
    last_link = feed.get("last_link")
    max_item = feed.get("max_item", 25)

    try:
        rss_xml = await get_feed(feed["url"])
    except:
        return None

    parser = feedparser.parse(rss_xml)
    item_list = parse_entries(parser.entries[:max_item], last_link)
    if not item_list:
        return None
    feed["last_link"] = item_list[0][2]

    article_list = await async_map(
        lambda item: create_article(item[0], item[1]), item_list
    )
    if not article_list:
        return None

    return Section(title, article_list)


@retry(stop=stop_after_attempt(3), wait=wait_random(min=1, max=3))
async def get_feed(url):
    async with aiohttp.request("GET", url) as response:
        if(response.status > 399):
            raise IOError("connect error!")

        return await response.text()


def parse_entries(entries, last_link):
    item_list = []
    for entry in entries:
        link = entry.link
        if last_link == link:
            break

        title = entry.title
        rawdata = entry.get("description")
        if not rawdata:
            rawdata = entry.content[0].value
        item_list.append((title, rawdata, link))

    return item_list


async def create_article(title, rawdata):
    content, image_list = await sanitize_content(rawdata)

    description = extract_description(content)
    if not description:
        return None

    article_id = uuid.uuid4().hex
    await write_article(article_id, title, content)

    return Article(article_id, title, description, image_list)


async def sanitize_content(rawdata):
    cleaner = Cleaner(allow_tags=["div", "p", "figure", "img", "figcaption"],
                      safe_attrs_only=True, safe_attrs=["src"])
    cleaned_html = cleaner.clean_html(rawdata)

    parser = lxml.html.fromstring(cleaned_html)
    img_list = parser.xpath("//img")
    image_list = await async_map(
        lambda img: create_image(img.get("src")), img_list
    )
    for i, img in enumerate(img_list):
        img.set("src", f"{image_list[i]}.gif")
    content = lxml.html.tostring(parser, encoding="unicode")

    return content, image_list


def extract_description(content):
    cleaner = Cleaner(kill_tags=["figure"])
    cleaned_html = cleaner.clean_html(content)

    parser = lxml.html.fromstring(cleaned_html)
    line_list = parser.xpath("//text()")

    if line_list:
        return line_list[0]
    return None


async def write_article(article_id, title, content):
    env = Environment(loader=FileSystemLoader("templates"), enable_async=True)
    template = env.get_template("article.html")
    html = await template.render_async(title=title, content=content)
    async with aiofiles.open(f"content/{article_id}.html", "w") as f:
        await f.write(html)


async def create_image(url):
    try:
        content = await download_image(url)
        image_id = uuid.uuid4().hex
        await save_image(image_id, content)
        return image_id
    except:
        return "404"


@retry(stop=stop_after_attempt(3), wait=wait_random(min=1, max=3))
async def download_image(url):
    async with aiohttp.request("GET", url) as response:
        if(response.status > 399):
            raise IOError("connect error!")

        return await response.read()


async def save_image(image_id, content):
    image = Image.open(BytesIO(content))
    image.thumbnail((600, 800))
    image = image.convert("L")
    image_stream = BytesIO()
    image.save(image_stream, format="GIF")

    async with aiofiles.open(f"content/{image_id}.gif", "wb") as f:
        await f.write(image_stream.getvalue())


async def async_map(func, *iterables):
    return tuple(filter(None, await asyncio.gather(*map(func, *iterables))))


def load_json(filename):
    with open(filename, "r", encoding="utf-8") as f:
        content = json.load(f)
    return content


def dump_json(content, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False)


if __name__ == "__main__":
    setting = load_json("setting.json")

    magazine = asyncio.run(create_Magazine(setting))

    if magazine:
        dump_json(setting, "setting.json")
        print(1)
