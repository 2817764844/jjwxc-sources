# jjwxc-sources

晋江小说下载器的书源配置。

## 怎么用

下载器 GUI →「书源管理…」→ 远程书源地址填：

```
https://raw.githubusercontent.com/2817764844/jjwxc-sources/main/sources.json
```

或镜像（国内 raw 不通时用这个）：

```
https://cdn.jsdelivr.net/gh/2817764844/jjwxc-sources@main/sources.json
```

然后点「↓ 联网更新书源」。

## 合并规则

本地优先：同名源不会被远程覆盖，你调过的 delay、选择器都保留。
本地删过的源记在 `removed` 里，更新时不会被加回来。

## sources.json 字段

| 字段 | 说明 |
|---|---|
| `name` | 显示名，唯一 |
| `site` | 站点根地址 |
| `method` | GET / POST |
| `path` | 搜索路径，GET 用 `{kw}` 占位 |
| `data` | POST 表单字段 |
| `toc_sel` | 目录页章节链接的 CSS 选择器 |
| `content_sel` | 章节页正文容器的 CSS 选择器 |
| `delay` | 每章间隔秒 |
| `enabled` | 是否启用 |
| `verified` | 最近一次内容校验结果（`match` 低于 0.7 表示搜到的是别的书） |

## 关于 enabled: false 的几条

`biquges`、`xbiquwk`、`panxs` 标成 false 是因为实测发现：
搜「勿扰飞升」能命中，但抓下来的正文完全是另一本书（内容相似度 0.00–0.01）。
留在这里是给后来人提个醒，别当它们能用。

## 更新记录

- 2026-11-17 首次发布：9 条启用 + 3 条标注禁用
