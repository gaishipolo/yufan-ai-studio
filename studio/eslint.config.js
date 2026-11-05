import js from '@eslint/js';
import globals from 'globals';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

/**
 * ESLint flat config（v9）。
 *
 * 三层效力，与 docs/CODE_STYLE.md 对齐：
 *   1. 正确性   —— 未使用变量、any、hooks 依赖、console 残留
 *   2. 架构     —— 分层单向：pages → widgets → features → entities → shared，
 *                  反向 import 直接报错（CODE_STYLE 1.2），不靠人盯
 *   3. 可访问性 —— jsx-a11y recommended（CODE_STYLE 3.7）
 *
 * 分层用 no-restricted-imports 的 zones 实现。注意：同一条规则在多个 config 里
 * 出现时，后一个会**整体覆盖**前一个的选项 —— 所以每个文件组只写一处该规则，
 * 禁止项全部在 `restrictedPatterns()` 里一次给全。
 *
 * 已知边界：只拦 `@/xxx` 别名路径，相对路径 `../../features/...` 仍可绕过，
 * 由 review checklist 兜底。
 */

const LAYERS = ['app', 'modules', 'pages', 'widgets', 'features', 'entities', 'shared'];

const HTTP_PATTERN = {
  group: ['@/shared/api/http'],
  message:
    '组件不要直接调用 http：请求写在 entities/*/api/*.api.ts，hook 写在 *.queries.ts（CODE_STYLE 3.3）。',
};

/** 某层禁用的 import：上方的层 +（可选）直接调 http */
function restrictedPatterns(layer, { blockHttp = false } = {}) {
  const forbidden = LAYERS.slice(0, LAYERS.indexOf(layer)).map((upper) => ({
    group: [`@/${upper}/*`],
    message: `分层违规：${layer} 层不允许 import ${upper} 层（依赖只能单向向下，见 CODE_STYLE 1.2）。`,
  }));
  return blockHttp ? [...forbidden, HTTP_PATTERN] : forbidden;
}

function restrictedRule(patterns) {
  return ['error', { patterns }];
}

const reactRules = {
  ...reactHooks.configs.recommended.rules,
  'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

  'no-console': ['error', { allow: ['warn', 'error'] }],
  'no-debugger': 'error',
  eqeqeq: ['error', 'smart'],
  'prefer-const': 'error',
  '@typescript-eslint/no-explicit-any': 'error',
  '@typescript-eslint/no-unused-vars': [
    'error',
    { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
  ],
  '@typescript-eslint/consistent-type-imports': [
    'error',
    { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
  ],
};

export default tseslint.config(
  {
    ignores: [
      'dist',
      // 验证 mock 分包时的临时产物（`VITE_USE_MOCK=true vite build --outDir dist-mock`）
      'dist-mock',
      'coverage',
      'node_modules',
      // 后端 export-openapi 的生成物（文件头已声明「请勿手工编辑」），格式由生成器决定。
      // 它仍参与 tsc 类型检查，只是不按手写代码的规范要求。
      'src/shared/api/schema.ts',
    ],
  },

  js.configs.recommended,
  ...tseslint.configs.recommended,

  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: reactRules,
  },

  // —— 可访问性 ——
  {
    files: ['**/*.tsx'],
    ...jsxA11y.flatConfigs.recommended,
  },

  // —— 分层单向（每层只写一次 no-restricted-imports）——
  {
    // 模块清单是「最上层」的纵向切片：只能向下依赖，不能反向依赖 app，也不能横向依赖其它模块
    files: ['src/modules/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrictedRule([
        {
          group: ['@/app/*'],
          message: '模块不要 import app 层：注册是 app 单向发现模块，反向依赖会形成循环。',
        },
        {
          group: ['@/modules/*'],
          message: '模块之间不要互相 import：需要复用的东西下沉到 shared / features / entities。',
        },
      ]),
    },
  },
  {
    files: ['src/shared/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrictedRule(restrictedPatterns('shared')),
    },
  },
  {
    files: ['src/entities/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrictedRule(restrictedPatterns('entities')),
    },
  },
  {
    // 实体层 UI 组件额外禁止直接调 http（api 子目录除外）
    files: ['src/entities/*/ui/**/*.tsx'],
    rules: {
      'no-restricted-imports': restrictedRule(restrictedPatterns('entities', { blockHttp: true })),
    },
  },
  {
    files: ['src/features/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrictedRule(restrictedPatterns('features', { blockHttp: true })),
    },
  },
  {
    files: ['src/widgets/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrictedRule(restrictedPatterns('widgets', { blockHttp: true })),
    },
  },
  {
    files: ['src/pages/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': restrictedRule(restrictedPatterns('pages', { blockHttp: true })),
    },
  },

  // —— 测试文件：放宽 Node/测试全局 ——
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**/*.ts'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },

  // —— 工具配置文件本身 ——
  {
    files: ['*.config.{js,ts}', 'postcss.config.js', 'tailwind.config.js'],
    languageOptions: { globals: globals.node },
  },

  // —— 脚手架脚本（Node ESM）——
  {
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
);
