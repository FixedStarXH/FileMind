# M14 PDF 与 PPTX 整页预览验收

## 实现

- 解析 worker 使用本地 LibreOffice 将不超过 100 页的 PPTX 转为 PDF 预览副本；原始 PPTX 文本仍由现有解析器提取，供检索和引用定位。
- 预览副本保存在独立的 `filemind_previews` 持久卷。API 以只读方式访问并校验文件所有权；永久删除文档时清理对应副本。
- 转换失败或超过页数限制时回退到原有图片/文字近似布局，文字提取与原文件下载不受影响。
- PDF 和渲染后的 PPTX 共用 PDF.js 整页阅读器，提供适合页面、适合宽度、缩放、文字栏开关和页码跳转。
- 解析 worker 单独安装 LibreOffice 与中文字体；演示 Compose 将其内存上限设为 1.5 GiB。

## 验收结果

- `docker compose build worker-ingestion` 成功；用户上传的《太原红色记忆——英雄城市的革命足迹.pptx》（26 页）重新解析成功，API 返回 `rendered_available=true`。
- Edge 桌面视口逐页检查第 1、2、13 页：均完整显示原始页面、无预览错误；标题、插图、时间线均保留。适合页面时画布约 815×458；隐藏文字栏并选适合宽度时约 1130×636。
- Edge 手机视口检查 PDF：翻页、适合页面/宽度、缩放和文字栏按钮均可见，页面不溢出视口。
- 演示账号新增的合成 PPTX（3 页）及 PDF（3 页）均可打开；PPTX 返回 `rendered_available=true`。
- 未登录访问渲染 PDF 接口返回 401。
- `npm run build`、Ruff 检查与格式检查、mypy 检查、`docker compose config --quiet` 均通过。

演示账号新增文件、原始用户 PPT、生成的 PDF 预览副本及验收截图均不提交到 Git。
