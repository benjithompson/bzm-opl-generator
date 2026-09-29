// Hook rules only: the typecheck and the tests cover the rest.
//
// The parser is Babel's rather than typescript-eslint's: that one needs the
// TypeScript JS API, which TypeScript 7 no longer ships. Parsing is all a
// syntax-level rule needs, and `tsc --noEmit` still does the type checking.
import babelParser from "@babel/eslint-parser";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["node_modules/", "dist/", "scripts/"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: babelParser,
      parserOptions: {
        requireConfigFile: false,
        babelOptions: {
          babelrc: false,
          configFile: false,
          presets: [
            ["@babel/preset-typescript", { isTSX: true, allExtensions: true }],
            ["@babel/preset-react", { runtime: "automatic" }],
          ],
        },
      },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": ["error", { additionalHooks: "^useResource$" }],
    },
  },
];
