# 协作开发约定

## 分支

- `main`：保持可运行、可评审。
- `feature/<name>`：新功能。
- `fix/<name>`：缺陷修复。
- `docs/<name>`：文档改动。

开始开发前先同步 `main`，一个分支尽量只解决一个问题。

## 提交

提交信息使用简短、明确的动作描述，推荐格式：

```text
feat: add transcription task model
fix: handle empty upload safely
docs: clarify MVP acceptance criteria
```

不要提交 `.env`、密钥、Cookie、数据库、媒体、模型权重、运行输出或 `references/github/` 下的第三方仓库。

## Pull Request

PR 应说明：

- 做了什么以及为什么
- 如何验证
- 页面或输出变化（适用时附截图）
- 已知限制、风险或后续事项

合并前至少由另一位协作者完成一次评审。默认使用 Squash merge，保持 `main` 历史简洁。

## 代码风格

- **Python**: 遵循 PEP 8，使用 Ruff 进行代码检查和格式化。
- **TypeScript**: 遵循 ESLint 规则。
- **提交信息**: 使用约定式提交格式（见上文）。

## 测试要求

- 单元测试覆盖率应保持较高水平。
- 新功能需附带相应的测试用例。
- 修复 bug 时需补充回归测试。

## 首期完成标准

具体功能与验收标准以 [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) 为准；如果实现与文档冲突，应先在 PR 中说明并确认范围。

