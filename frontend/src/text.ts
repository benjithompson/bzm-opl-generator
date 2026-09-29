// Counting things in a sentence.

/** "1 warning", "3 warnings". `many` defaults to the regular form, so only an
 *  irregular plural has to be spelled out at the call site. */
export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

/** "1 passed", "3 to apply": a count whose word does not inflect. */
export const counted = (n: number, word: string) => `${n} ${word}`;
