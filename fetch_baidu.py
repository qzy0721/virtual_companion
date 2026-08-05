"""百度百科爬取模块

通过 curl_cffi 请求百度百科，提取词条标题、摘要、基本信息和正文全文。
Cookie 存放在 .env 的 BAIDU_COOKIE 中，过期后需从浏览器重新抓取。

异常体系：
    BaikeError           — 基类
    BaikeNotFoundError   — 词条不存在 (HTTP 404)
    BaikeCookieExpiredError — Cookie 过期 (HTTP 403)
    BaikeRequestError    — 网络请求失败
    BaikeParseError      — 页面解析异常
"""
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import re
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ==================== 异常定义 ====================

class BaikeError(Exception):
    """百度百科爬取异常基类"""
    pass


class BaikeNotFoundError(BaikeError):
    """词条不存在 (HTTP 404)"""
    pass


class BaikeCookieExpiredError(BaikeError):
    """Cookie 过期 (HTTP 403)，需更新 .env 中的 BAIDU_COOKIE"""
    pass


class BaikeRequestError(BaikeError):
    """网络请求失败"""
    pass


class BaikeParseError(BaikeError):
    """页面解析异常"""
    pass


class BaikeURLError(BaikeError):
    """输入的 URL 不是有效的百度百科链接"""
    pass


# ==================== 爬取类 ====================

class BaikeCrawler:
    """百度百科词条爬取器。

    用法::

        crawler = BaikeCrawler()                # 从 .env 读取 Cookie
        crawler = BaikeCrawler(cookie="...")    # 或手动传入

        try:
            # 按名字爬取
            result = crawler.crawl("长城")
            # 按完整 URL 爬取（解决歧义词条）
            result = crawler.crawl("https://baike.baidu.com/item/长城/14251")
            print(result.title)
            print(result.summary)
            print(result.info)
            print(result.content)       # list[dict]
            print(result.content_text)  # 纯文本
        except BaikeNotFoundError:
            print("词条不存在")
        except BaikeCookieExpiredError:
            print("Cookie 过期，请更新")
        except BaikeURLError:
            print("URL 不是有效的百度百科链接")
        except BaikeError as e:
            print(f"爬取失败: {e}")
    """

    _DEFAULT_HEADERS = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "connection": "keep-alive",
        "host": "baike.baidu.com",
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
    }

    # 非正文元素的 class 关键词
    _SKIP_CLASSES = ("editLemma", "lemmaPicture", "value-video", "titleLine")

    def __init__(self, cookie: str | None = None, timeout: int = 15):
        """
        Args:
            cookie: 百度百科 Cookie，为 None 时从 .env 的 BAIDU_COOKIE 读取
            timeout: 请求超时秒数
        """
        self.cookie = cookie if cookie is not None else os.getenv("BAIDU_COOKIE", "")
        self.timeout = timeout

    # -------------------- 公开接口 --------------------

    def crawl(self, query: str) -> "BaikeResult":
        """爬取百度百科词条，支持词条名或完整 URL。

        自动判断输入类型：
          - 以 http 开头 → 视为完整 URL（解决歧义词条，如 "长城" 有多个义项）
          - 否则 → 视为词条名，自动拼接百科 URL

        Args:
            query: 词条名（如 "Python"、"长城"）或完整百科 URL
                   （如 "https://baike.baidu.com/item/长城/14251"）

        Returns:
            BaikeResult: 结构化结果对象

        Raises:
            BaikeNotFoundError: 词条不存在
            BaikeCookieExpiredError: Cookie 过期
            BaikeURLError: 输入的 URL 不是有效的百度百科链接
            BaikeRequestError: 网络请求失败
            BaikeParseError: 页面解析异常
        """
        url = self._resolve_url(query)
        headers = {**self._DEFAULT_HEADERS, "cookie": self.cookie}
        soup, actual_url = self._request(url, headers)

        try:
            title = self._parse_title(soup, query)
            summary = self._parse_summary(soup)
            info = self._parse_info(soup)
            content = self._parse_content(soup)
        except Exception as e:
            logger.error("页面解析异常: %s", e)
            raise BaikeParseError(f"页面解析异常: {e}") from e

        content_chars = sum(len(s["text"]) for s in content if s["type"] == "paragraph")
        logger.info("百科爬取成功: %s, 摘要 %d 字, 基本信息 %d 条, 正文 %d 字",
                    title, len(summary), len(info), content_chars)

        return BaikeResult(
            title=title,
            summary=summary,
            info=info,
            content=content,
            url=actual_url,
        )

    # -------------------- URL 解析 --------------------

    @staticmethod
    def _resolve_url(query: str) -> str:
        """将用户输入解析为百度百科 URL。

        - 以 http 开头 → 视为完整 URL，校验是否为百度百科域名
        - 否则 → 视为词条名，拼接百科 URL
        """
        q = query.strip()
        if q.startswith("http://") or q.startswith("https://"):
            # 完整 URL：校验域名
            if "baike.baidu.com" not in q:
                raise BaikeURLError(
                    f"不是百度百科链接，仅支持 baike.baidu.com 域名: {q}"
                )
            return q
        # 词条名 → 拼接 URL
        return f"https://baike.baidu.com/item/{quote(q)}"

    # -------------------- 网络请求 --------------------

    def _request(self, url: str, headers: dict) -> tuple[BeautifulSoup, str]:
        """发起请求并返回 (BeautifulSoup, 实际URL)，处理 HTTP 错误。"""
        try:
            resp = requests.get(url, headers=headers, impersonate="chrome110", timeout=self.timeout)
        except requests.Errors.ConnectionError as e:
            logger.error("连接百科失败: %s", e)
            raise BaikeRequestError(f"连接失败: {e}") from e
        except requests.Errors.Timeout as e:
            logger.error("请求百科超时: %s", e)
            raise BaikeRequestError(f"请求超时: {e}") from e
        except Exception as e:
            logger.error("请求百科失败: %s", e)
            raise BaikeRequestError(f"请求失败: {e}") from e

        if resp.status_code == 404:
            logger.error("百科返回 404，词条不存在")
            raise BaikeNotFoundError("词条不存在")

        if resp.status_code == 403:
            logger.error("百科返回 403，Cookie 可能已过期")
            raise BaikeCookieExpiredError("Cookie 过期，请更新 .env 中的 BAIDU_COOKIE")

        if resp.status_code != 200:
            logger.error("百科返回 %d", resp.status_code)
            raise BaikeRequestError(f"HTTP {resp.status_code}")

        resp.encoding = "utf-8"
        actual_url = str(resp.url)  # 跟踪重定向后的真实 URL
        try:
            return BeautifulSoup(resp.text, "html.parser"), actual_url
        except Exception as e:
            logger.error("HTML 解析失败: %s", e)
            raise BaikeParseError(f"HTML 解析失败: {e}") from e

    # -------------------- 解析各部分 --------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理百科文本：去引用标记 [1]、[20-21] 等，合并空白"""
        text = re.sub(r'\[\d+[-\d]*\]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @staticmethod
    def _parse_title(soup: BeautifulSoup, fallback: str) -> str:
        """提取词条标题"""
        h1 = soup.find("h1", class_="J-lemma-title")
        if h1:
            return h1.get_text(strip=True)
        # 兜底：任意 h1
        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else fallback

    def _parse_summary(self, soup: BeautifulSoup) -> str:
        """提取摘要"""
        summary_div = soup.find("div", class_="J-summary")
        if summary_div:
            return self._clean_text(summary_div.get_text())
        # 兜底：meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            return self._clean_text(meta_desc["content"])
        return ""

    @staticmethod
    def _parse_info(soup: BeautifulSoup) -> dict[str, str]:
        """提取基本信息 key-value"""
        basic_info_div = soup.find("div", class_="J-basic-info")
        if not basic_info_div:
            # 兜底：匹配 basicInfo_ 前缀
            basic_info_div = soup.find("div", class_=re.compile(r"basicInfo_"))

        info: dict[str, str] = {}
        if basic_info_div:
            dts = basic_info_div.find_all("dt")
            dds = basic_info_div.find_all("dd")
            for dt, dd in zip(dts, dds):
                key = dt.get_text(strip=True).replace('\xa0', '')
                value = dd.get_text(strip=True).replace('\xa0', '')
                if key:
                    info[key] = value
        return info

    def _parse_content(self, soup: BeautifulSoup) -> list[dict]:
        """提取正文全文，保留标题层级结构。

        Returns:
            list[dict]: 每个元素为 {"type": "heading"|"paragraph", "level": int, "text": str}
            heading 的 level: 1=大标题, 2=小标题
        """
        content_div = soup.find("div", class_="J-lemma-content")
        if not content_div:
            # 兜底：匹配 mainContent 前缀 class
            content_div = soup.find("div", class_=re.compile(r"mainContent"))

        if not content_div:
            return []

        sections: list[dict] = []

        for child in content_div.find_all(["div", "h2", "h3"], class_=True):
            cls = " ".join(child.get("class", []))

            # 跳过非正文元素
            if any(skip in cls for skip in self._SKIP_CLASSES):
                continue

            # 标题检测：class 含 paraTitle
            if "paraTitle" in cls:
                if "level-2" in cls:
                    sections.append({"type": "heading", "level": 2,
                                     "text": self._clean_text(child.get_text())})
                else:
                    sections.append({"type": "heading", "level": 1,
                                     "text": self._clean_text(child.get_text())})
                continue

            # 段落检测：class 以 para_ 或 para- 开头（带哈希后缀），排除 paraTitle
            if re.search(r"(?:^|\s)para[_\-]", cls) and "paraTitle" not in cls:
                text = self._clean_text(child.get_text())
                if text:
                    sections.append({"type": "paragraph", "level": 0, "text": text})

        return sections


# ==================== 结果数据类 ====================

class BaikeResult:
    """百度百科词条爬取结果。

    Attributes:
        title: 词条标题
        summary: 摘要
        info: 基本信息 key-value
        content: 正文全文 [{"type","level","text"}, ...]
        url: 请求的 URL
    """

    def __init__(self, title: str, summary: str, info: dict[str, str],
                 content: list[dict], url: str):
        self.title = title
        self.summary = summary
        self.info = info
        self.content = content
        self.url = url

    @property
    def content_text(self) -> str:
        """将 content 列表转为纯文本，便于阅读或喂给 LLM。"""
        lines = []
        for sec in self.content:
            if sec["type"] == "heading":
                prefix = "#" if sec["level"] == 1 else "##"
                lines.append(f"\n{prefix} {sec['text']}\n")
            else:
                lines.append(sec["text"])
        return "\n".join(lines)

    def __repr__(self) -> str:
        n_paras = sum(1 for s in self.content if s["type"] == "paragraph")
        n_chars = sum(len(s["text"]) for s in self.content if s["type"] == "paragraph")
        return (f"BaikeResult(title={self.title!r}, "
                f"summary={len(self.summary)}字, "
                f"info={len(self.info)}条, "
                f"正文={n_paras}段/{n_chars}字, "
                f"url={self.url!r})")


# ==================== 向后兼容的函数式接口 ====================

def crawl_baike(query: str) -> dict:
    """爬取百度百科词条，返回 dict（向后兼容接口）。

    推荐使用 BaikeCrawler.crawl() 获取 BaikeResult 对象，
    该函数仅为旧代码兼容而保留。

    Args:
        query: 词条名或完整百科 URL

    Returns:
        dict: 成功时含 title/summary/info/content/url；
              失败时含 error 键
    """
    crawler = BaikeCrawler()
    try:
        r = crawler.crawl(query)
        return {
            "title": r.title,
            "summary": r.summary,
            "info": r.info,
            "content": r.content,
            "url": r.url,
        }
    except BaikeNotFoundError:
        return {"error": "词条不存在"}
    except BaikeCookieExpiredError:
        return {"error": "Cookie 过期，请更新 .env 中的 BAIDU_COOKIE"}
    except BaikeURLError as e:
        return {"error": str(e)}
    except BaikeRequestError as e:
        return {"error": f"请求失败: {e}"}
    except BaikeParseError as e:
        return {"error": f"解析失败: {e}"}
    except BaikeError as e:
        return {"error": str(e)}


def content_to_plain(content: list[dict]) -> str:
    """将 content 列表转为纯文本（向后兼容接口）。"""
    lines = []
    for sec in content:
        if sec["type"] == "heading":
            prefix = "#" if sec["level"] == 1 else "##"
            lines.append(f"\n{prefix} {sec['text']}\n")
        else:
            lines.append(sec["text"])
    return "\n".join(lines)


# ==================== 命令行测试 ====================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys

    term = sys.argv[1] if len(sys.argv) > 1 else "绫地宁宁"
    crawler = BaikeCrawler()

    try:
        r = crawler.crawl(term)
        print(f"标题: {r.title}")
        print(f"URL:  {r.url}")
        print(f"摘要: {r.summary[:200]}...")
        print(f"信息: {len(r.info)} 条")
        for k, v in list(r.info.items())[:5]:
            print(f"  {k}: {v}")
        print(f"\n{'='*40}")
        print(f"正文 ({len(r.content)} 个段落/标题):")
        print(r.content_text)
    except BaikeNotFoundError:
        print(f"词条「{term}」不存在")
    except BaikeCookieExpiredError:
        print("Cookie 过期，请更新 .env 中的 BAIDU_COOKIE")
    except BaikeURLError as e:
        print(f"URL 错误: {e}")
    except BaikeError as e:
        print(f"爬取失败: {e}")
