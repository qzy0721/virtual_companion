# 静态展示页部署

该目录没有构建步骤，可直接作为静态站点根目录上传。入口为 `index.html`，所有页面与资源均使用相对路径，因此可部署在独立域名或子路径。

## 发布内容

- `index.html`、`404.html`、`pages/`：页面。
- `css/`、`js/`、`assets/`：本地静态资源，不依赖外部 CDN。
- `downloads/VirtualCompanion-Setup-0.2.0.exe`：Windows 安装包。
- `downloads/sourcecode.zip`：源码包。
- `_headers`：Cloudflare Pages / Netlify 可直接识别的安全与缓存响应头。
- `robots.txt`：允许搜索引擎抓取；绑定正式域名后可再生成带绝对地址的 sitemap。

## 上传前检查

安装包约 190.4 MiB。若静态托管平台限制单文件大小，请把安装包上传到同域对象存储或 Release 服务，再将 `pages/download.html` 中的两个相对地址替换为最终 HTTPS 地址。不要使用临时分享链接。

本地预览可在项目根目录执行：

```powershell
D:\Anaconda\envs\virtual_accompany\python.exe -m http.server 4173 --directory static-display-page
```
