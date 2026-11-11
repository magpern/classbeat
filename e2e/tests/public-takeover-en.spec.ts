import { defineTakeoverCells } from './public-takeover-cells'

// The takeover gate's cells for en (public-takeover-cells.ts). A separate file per language, named
// so the three sort into different Playwright shards (shards are contiguous in file-name order).
defineTakeoverCells('en')
