import asyncio
import json
import time
import uuid
from collections import namedtuple

import aiofiles
import aiohttp
import feedparser
import lxml.html
from jinja2 import Environment, FileSystemLoader
from lxml.html.clean import Cleaner
from tenacity import retry, stop_after_attempt, wait_random

Image = namedtuple("Image", "id, name, media_type")
Article = namedtuple("Article", "id, title")
Section = namedtuple("Section", "title, article_list")
Magazine = namedtuple("Magazine", "id, title, date, section_list, image_list")

image_list = []


async def create_Magazine(setting):
    title = setting.get("title", "Rss")
    date = time.strftime("%Y-%m-%d")
    feed_list = setting["feed_list"]

    section_list = await async_map(create_section, feed_list)
    if section_list:
        magazine = Magazine(
            uuid.uuid4().hex, title, date, section_list, image_list
        )
        await asyncio.gather(
            write_content(magazine),
            write_toc_html(magazine),
            write_toc_ncx(magazine)
        )
        return magazine

    return None


async def create_section(feed):
    title = feed["title"]
    last_link = feed.get("last_link")
    max_item = feed.get("max_item", 25)

    parser = feedparser.parse(await get_feed(feed["url"]))
    entries = find(
        lambda entry: entry.link == last_link,
        parser.entries[:max_item]
    )
    if not entries:
        return None

    article_list = await async_map(
        lambda entry: create_article(entry), entries
    )

    if entries:
        feed["last_link"] = entries[0].link

    return Section(title, article_list)


async def create_article(entry):
    id = uuid.uuid4().hex
    title = entry.title
    rawdata = entry.get("description", None)
    if not rawdata:
        rawdata = entry.content[0].value

    cleaned_html = clean_html(rawdata)
    if not cleaned_html:
        return None

    parser = lxml.html.fromstring(cleaned_html)
    img_list = parser.xpath("//img")
    await async_map(
        lambda img: create_image(img), img_list
    )

    content = lxml.html.tostring(parser, encoding="unicode")

    await write_article(id, title, content)

    return Article(id, title)


async def create_image(img):
    id = uuid.uuid4().hex
    url = img.get("src")
    content, media_type = await download_image(url)

    name = f"{id}.{postfix(media_type)}"
    async with aiofiles.open(f"content/{name}", "wb") as f:
        await f.write(content)

    img.set("src", name)
    image_list.append(Image(id, name, media_type))


def postfix(media_type):
    if media_type == "image/jpeg":
        return "jpg"
    return media_type.split('/')[-1]

def clean_html(html):
    cleaner = Cleaner(allow_tags=["div", "p", "figure", "img", "figcaption"],
                      safe_attrs_only=True, safe_attrs=["src"])
    return cleaner.clean_html(html)


@retry(stop=stop_after_attempt(3), wait=wait_random(min=1, max=3))
async def get_feed(url):
    async with aiohttp.request("GET", url) as response:
        if(response.status > 399):
            raise IOError("connect error!")

        return await response.text()


@retry(stop=stop_after_attempt(3), wait=wait_random(min=1, max=3))
async def download_image(url):
    async with aiohttp.request("GET", url) as response:
        if(response.status > 399):
            raise IOError("connect error!")

        content = await response.read()
        media_type = response.headers["Content-Type"]
        return content, media_type


async def write_article(id, title, content):
    env = Environment(loader=FileSystemLoader("templates"), enable_async=True)
    template = env.get_template("article.html")
    article = await template.render_async(title=title, content=content)
    async with aiofiles.open(f"content/{id}.html", "w") as f:
        await f.write(article)


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


async def async_map(func, iterables):
    return tuple(filter(None, await asyncio.gather(*map(func, iterables))))


def find(func, iterables):
    for index, entry in enumerate(iterables):
        if func(entry):
            return iterables[:index]

    return iterables


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
