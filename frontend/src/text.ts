// Counting things in a sentence.
//
// One helper for both summary lines. preflight.ts and suggestions.ts each grew
// a `plural` of its own -- same name, same job, incompatible signatures, one
// file apart -- which is the pair that stays wrong once the two start
// disagreeing about what a caller may leave out.

/** "1 warning", "3 warnings". `many` defaults to the regular form, so only an
 *  irregular plural has to be spelled out at the call site. */
export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

/** "1 passed", "3 to apply" -- a count of something whose word does not
 *  inflect. Its own function rather than `plural(n, w, w)`: the same word twice
 *  reads as a mistake, and the next reader has to compare two strings to find
 *  out that it is not one. */
export const counted = (n: number, word: string) => `${n} ${word}`;
