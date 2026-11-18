# 第三方声明

随仓库分发（`assets/vendor/`，未修改的 npm 发布文件，校验值见 `assets/vendor/README.md`）：

| 文件 | 来源 | 版本 | 许可证 |
| --- | --- | --- | --- |
| `marked.umd.js` | [marked](https://www.npmjs.com/package/marked) `lib/marked.umd.js` | 18.0.11 | MIT，`assets/vendor/marked.LICENSE.txt` |
| `purify.min.js` | [DOMPurify](https://www.npmjs.com/package/dompurify) `dist/purify.min.js` | 3.4.17 | Apache-2.0 或 MPL-2.0，`assets/vendor/dompurify.LICENSE-*.txt` |

仅用于测试、不随产品分发：`jsdom`（devDependency，MIT；完整依赖树见 `package-lock.json`）。

本仓库原创代码的许可证尚待作者确认，见 `docs/licensing.md`。
