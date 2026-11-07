---
name: bento
description: Modular grid layout with card-like blocks, clear hierarchy, soft spacing, and subtle visual contrast for organized, scannable interfaces.
license: MIT
metadata:
  author: typeui.sh
---

<!-- TYPEUI_SH_MANAGED_START -->
# Bento Design System Skill (Universal)

## Mission
You are an expert design-system guideline author for Bento.
Create practical, implementation-ready guidance that can be directly used by engineers and designers.

## Brand
The bento box style is an innovative design approach that uses a grid layout to present content in visually appealing blocks of varying sizes.

## Style Foundations
- Visual style: modern, clean
- Typography scale: 12/14/16/20/24/32 | Fonts: primary=Inter, display=Inter, mono=JetBrains Mono | weights=100, 200, 300, 400, 500, 600, 700, 800, 900
- Color palette: primary, neutral, success, warning, danger | Tokens: primary=#FAD4C0, secondary=#80A1C1, success=#16A34A, warning=#D97706, danger=#DC2626, surface=#FFF5E6, text=#111827
- Spacing scale: 4/8/12/16/24/32


## Accessibility
WCAG 2.2 AA, keyboard-first interactions, visible focus states

## Writing Tone
concise, confident, helpful

## Rules: Do
- prefer semantic tokens over raw values
- preserve visual hierarchy
- keep interaction states explicit

## Rules: Don't
- avoid low contrast text
- avoid inconsistent spacing rhythm
- avoid ambiguous labels

## Expected Behavior
- Follow the foundations first, then component consistency.
- When uncertain, prioritize accessibility and clarity over novelty.
- Provide concrete defaults and explain trade-offs when alternatives are possible.
- Keep guidance opinionated, concise, and implementation-focused.

## Guideline Authoring Workflow
1. Restate the design intent in one sentence before proposing rules.
2. Define tokens and foundational constraints before component-level guidance.
3. Specify component anatomy, states, variants, and interaction behavior.
4. Include accessibility acceptance criteria and content-writing expectations.
5. Add anti-patterns and migration notes for existing inconsistent UI.
6. End with a QA checklist that can be executed in code review.

## Required Output Structure
When generating design-system guidance, use this structure:
- Context and goals
- Design tokens and foundations
- Component-level rules (anatomy, variants, states, responsive behavior)
- Accessibility requirements and testable acceptance criteria
- Content and tone standards with examples
- Anti-patterns and prohibited implementations
- QA checklist

## Component Rule Expectations
- Define required states: default, hover, focus-visible, active, disabled, loading, error (as relevant).
- Describe interaction behavior for keyboard, pointer, and touch.
- State spacing, typography, and color-token usage explicitly.
- Include responsive behavior and edge cases (long labels, empty states, overflow).

## Quality Gates
- No rule should depend on ambiguous adjectives alone; anchor each rule to a token, threshold, or example.
- Every accessibility statement must be testable in implementation.
- Prefer system consistency over one-off local optimizations.
- Flag conflicts between aesthetics and accessibility, then prioritize accessibility.

## Example Constraint Language
- Use "must" for non-negotiable rules and "should" for recommendations.
- Pair every do-rule with at least one concrete don't-example.
- If introducing a new pattern, include migration guidance for existing components.

<!-- TYPEUI_SH_MANAGED_END -->

## 本專案的例外（路考網站；跟上面衝突時以這裡為準）
- 字型用系統字，不用 Inter、JetBrains Mono：Inter 沒有中文；也不從外站載字型，學校網路擋外站時頁面會卡住。
  - 內文：`"Noto Sans TC", "Microsoft JhengHei", "PingFang TC", system-ui, sans-serif`
  - 等寬：`ui-monospace, Consolas, "Courier New", monospace`
- 字級多一級 48px，只給成績大字用。
- 對比（WCAG 2.2 AA，文字 ≥ 4.5:1）：
  - 文字用加深的 `*-ink` 版本：secondary、danger、warning、success 各一個，淺色深色各一個值。
  - `#80A1C1`（secondary）只用在框線和裝飾，不用在文字上。
  - 桃色 primary 上面一律放深色字。
- 深色模式用 CSS `light-dark()`，淺色深色的值各寫一次。不支援的舊瀏覽器停在淺色版；全螢幕播放器在那裡改用淺色底。
- 不在 4/8/12/16/24/32 級距裡、但允許的數值。各自有自己的變數，不借用間距變數：
  - 頁尾多留的一段 64px（兩個 32，`--page-end`）
  - 框線 1px、2px、6px（`--rule`、`--rule-strong`、`--rule-accent`）
  - 焦點框粗 3px（`--focus-ring`）
  - 程式碼字級 `.95em`
  - 進度條高度 10px
  - 勾選框往下移 6px
  - 連結底線距離 3px
  - 焦點框位移 2px
  - 按鈕滑過時浮起 1px
- 只給報讀軟體念的字（`.sr-only`）用固定的 1px、-1px 寫法，那不是間距，直接寫在 CSS 裡。
