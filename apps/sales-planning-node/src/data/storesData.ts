import type { Store, StoreStatus } from '../types';

// Raw source: "2026 _All Stores _Total Sales By Store.csv" (FY2026 actuals) joined against
// "2025 _ Baseline _ Total Sales By Store.csv" (FY2025 actuals) by store name.
interface RawStoreRow {
  name: string;
  fy2026Actual: number;
  transactionCount2026: number;
  avgTransaction2026: number;
  itemsSold2026: number;
  avgItemValue2026: number;
  donationCount2026: number;
  fy2025Actual: number;
}

const RAW_STORES: RawStoreRow[] = [
  { name: 'ADC - Leawood', fy2026Actual: 192308.53, transactionCount2026: 8604, avgTransaction2026: 22.35, itemsSold2026: 34833, avgItemValue2026: 5.52, donationCount2026: 12531, fy2025Actual: 360732.11 },
  { name: 'Alton - Homer Adams', fy2026Actual: 1493493.34, transactionCount2026: 75626, avgTransaction2026: 19.75, itemsSold2026: 353816, avgItemValue2026: 4.22, donationCount2026: 19880, fy2025Actual: 3194830.92 },
  { name: 'Arnold - Richardson', fy2026Actual: 1423030.18, transactionCount2026: 66350, avgTransaction2026: 21.45, itemsSold2026: 303143, avgItemValue2026: 4.69, donationCount2026: 24224, fy2025Actual: 2788627.31 },
  { name: 'Belleville - Belt', fy2026Actual: 993212.25, transactionCount2026: 51659, avgTransaction2026: 19.23, itemsSold2026: 238808, avgItemValue2026: 4.16, donationCount2026: 12440, fy2025Actual: 2024260.20 },
  { name: 'Blue Springs - MO-7', fy2026Actual: 578776.17, transactionCount2026: 28626, avgTransaction2026: 20.22, itemsSold2026: 141359, avgItemValue2026: 4.09, donationCount2026: 7643, fy2025Actual: 1383817.47 },
  { name: 'Bonner Springs - 129th St', fy2026Actual: 741254.73, transactionCount2026: 33003, avgTransaction2026: 22.46, itemsSold2026: 182588, avgItemValue2026: 4.06, donationCount2026: 19848, fy2025Actual: 1441354.67 },
  { name: 'Brentwood - Manchester', fy2026Actual: 1020149.88, transactionCount2026: 48019, avgTransaction2026: 21.24, itemsSold2026: 182959, avgItemValue2026: 5.58, donationCount2026: 39949, fy2025Actual: 1888738.84 },
  { name: 'Cape Girardeau - Silver Springs', fy2026Actual: 1542298.45, transactionCount2026: 74140, avgTransaction2026: 20.80, itemsSold2026: 335751, avgItemValue2026: 4.59, donationCount2026: 17961, fy2025Actual: 3049717.74 },
  { name: 'Carbondale - Main', fy2026Actual: 1215726.31, transactionCount2026: 58057, avgTransaction2026: 20.94, itemsSold2026: 258161, avgItemValue2026: 4.71, donationCount2026: 14729, fy2025Actual: 2277653.15 },
  { name: 'Centralia - Broadway', fy2026Actual: 1015632.34, transactionCount2026: 42163, avgTransaction2026: 24.09, itemsSold2026: 230235, avgItemValue2026: 4.41, donationCount2026: 6949, fy2025Actual: 2169824.85 },
  { name: 'Chesterfield - Forum', fy2026Actual: 1105234.11, transactionCount2026: 57051, avgTransaction2026: 19.37, itemsSold2026: 232005, avgItemValue2026: 4.76, donationCount2026: 20613, fy2025Actual: 2208984.29 },
  { name: 'Chesterfield - Valley', fy2026Actual: 1119818.09, transactionCount2026: 52249, avgTransaction2026: 21.43, itemsSold2026: 238821, avgItemValue2026: 4.69, donationCount2026: 20330, fy2025Actual: 2262829.63 },
  { name: 'Columbia - Grindstone', fy2026Actual: 2533079.25, transactionCount2026: 101305, avgTransaction2026: 25.00, itemsSold2026: 453903, avgItemValue2026: 5.58, donationCount2026: 41944, fy2025Actual: 4707229.92 },
  { name: 'Eureka - Thresher', fy2026Actual: 0, transactionCount2026: 0, avgTransaction2026: 0, itemsSold2026: 0, avgItemValue2026: 0, donationCount2026: 0, fy2025Actual: 0 },
  { name: 'Farmington - Potosi', fy2026Actual: 1431705.44, transactionCount2026: 64981, avgTransaction2026: 22.03, itemsSold2026: 317551, avgItemValue2026: 4.51, donationCount2026: 14272, fy2025Actual: 2692902.91 },
  { name: 'Fenton - Gravois Bluffs', fy2026Actual: 1254523.50, transactionCount2026: 60403, avgTransaction2026: 20.77, itemsSold2026: 250701, avgItemValue2026: 5.00, donationCount2026: 9432, fy2025Actual: 0 },
  { name: 'Festus - Truman', fy2026Actual: 1252763.23, transactionCount2026: 63997, avgTransaction2026: 19.58, itemsSold2026: 280503, avgItemValue2026: 4.47, donationCount2026: 20095, fy2025Actual: 2467965.95 },
  { name: 'Florissant - Highway 67', fy2026Actual: 1434923.03, transactionCount2026: 66228, avgTransaction2026: 21.67, itemsSold2026: 275026, avgItemValue2026: 5.22, donationCount2026: 24987, fy2025Actual: 2762054.42 },
  { name: 'Glen Carbon - Junction', fy2026Actual: 1601740.14, transactionCount2026: 77533, avgTransaction2026: 20.66, itemsSold2026: 342141, avgItemValue2026: 4.68, donationCount2026: 34211, fy2025Actual: 3041419.55 },
  { name: 'Granite City - Nameoki', fy2026Actual: 1189514.41, transactionCount2026: 56502, avgTransaction2026: 21.05, itemsSold2026: 278292, avgItemValue2026: 4.27, donationCount2026: 9606, fy2025Actual: 2182029.98 },
  { name: 'Hannibal - Stardust', fy2026Actual: 1065815.73, transactionCount2026: 49914, avgTransaction2026: 21.35, itemsSold2026: 236932, avgItemValue2026: 4.50, donationCount2026: 8829, fy2025Actual: 2046918.42 },
  { name: 'Jefferson City - Ten Mile', fy2026Actual: 1460932.62, transactionCount2026: 65652, avgTransaction2026: 22.25, itemsSold2026: 308392, avgItemValue2026: 4.74, donationCount2026: 29936, fy2025Actual: 2568077.41 },
  { name: 'Jennings - Florissant', fy2026Actual: 880135.84, transactionCount2026: 44580, avgTransaction2026: 19.74, itemsSold2026: 192640, avgItemValue2026: 4.57, donationCount2026: 10205, fy2025Actual: 1782288.04 },
  { name: 'Kansas City - E 63rd', fy2026Actual: 466549.05, transactionCount2026: 22069, avgTransaction2026: 21.14, itemsSold2026: 116986, avgItemValue2026: 3.99, donationCount2026: 4785, fy2025Actual: 840230.50 },
  { name: 'Kansas City - Main St', fy2026Actual: 596186.71, transactionCount2026: 30560, avgTransaction2026: 19.51, itemsSold2026: 137680, avgItemValue2026: 4.33, donationCount2026: 7261, fy2025Actual: 754687.44 },
  { name: 'Kansas City - N Oak', fy2026Actual: 867571.27, transactionCount2026: 41405, avgTransaction2026: 20.95, itemsSold2026: 217152, avgItemValue2026: 4.00, donationCount2026: 12602, fy2025Actual: 1754631.66 },
  { name: 'Lake St. Louis - Robert Raymond', fy2026Actual: 1524948.34, transactionCount2026: 68948, avgTransaction2026: 22.12, itemsSold2026: 326562, avgItemValue2026: 4.67, donationCount2026: 30849, fy2025Actual: 3351028.62 },
  { name: 'Lawrence - 31st St', fy2026Actual: 1115465.03, transactionCount2026: 59072, avgTransaction2026: 18.88, itemsSold2026: 292035, avgItemValue2026: 3.82, donationCount2026: 31353, fy2025Actual: 2059763.04 },
  { name: 'Leavenworth - Broadway', fy2026Actual: 717877.76, transactionCount2026: 32661, avgTransaction2026: 21.98, itemsSold2026: 186109, avgItemValue2026: 3.86, donationCount2026: 9362, fy2025Actual: 1341927.19 },
  { name: "Lee's Summit - Ward", fy2026Actual: 776925.40, transactionCount2026: 38755, avgTransaction2026: 20.05, itemsSold2026: 204360, avgItemValue2026: 3.80, donationCount2026: 23678, fy2025Actual: 1342416.33 },
  { name: 'Liberty - N Cedar', fy2026Actual: 792443.91, transactionCount2026: 34422, avgTransaction2026: 23.02, itemsSold2026: 198128, avgItemValue2026: 4.00, donationCount2026: 16120, fy2025Actual: 1220025.19 },
  { name: 'Manchester - Manchester Rd', fy2026Actual: 1216452.97, transactionCount2026: 67955, avgTransaction2026: 17.90, itemsSold2026: 278088, avgItemValue2026: 4.37, donationCount2026: 23042, fy2025Actual: 2379254.64 },
  { name: 'Manhattan - Poyntz', fy2026Actual: 1247271.87, transactionCount2026: 61373, avgTransaction2026: 20.32, itemsSold2026: 316746, avgItemValue2026: 3.94, donationCount2026: 24891, fy2025Actual: 2427117.04 },
  { name: 'Marion - Outer', fy2026Actual: 1447683.73, transactionCount2026: 67324, avgTransaction2026: 21.50, itemsSold2026: 318824, avgItemValue2026: 4.54, donationCount2026: 11935, fy2025Actual: 2480810.96 },
  { name: 'Mexico - Clark', fy2026Actual: 1001202.98, transactionCount2026: 45786, avgTransaction2026: 21.87, itemsSold2026: 231386, avgItemValue2026: 4.33, donationCount2026: 3666, fy2025Actual: 2018111.15 },
  { name: 'Moberly - Morley', fy2026Actual: 1193971.13, transactionCount2026: 54726, avgTransaction2026: 21.82, itemsSold2026: 267676, avgItemValue2026: 4.46, donationCount2026: 8766, fy2025Actual: 2214266.03 },
  { name: "O'Fallon - Auto Court", fy2026Actual: 1208732.50, transactionCount2026: 58309, avgTransaction2026: 20.73, itemsSold2026: 272813, avgItemValue2026: 4.43, donationCount2026: 15850, fy2025Actual: 1868695.62 },
  { name: "O'Fallon - Highway K", fy2026Actual: 1152368.08, transactionCount2026: 63542, avgTransaction2026: 18.14, itemsSold2026: 265342, avgItemValue2026: 4.34, donationCount2026: 27254, fy2025Actual: 2259839.19 },
  { name: "O'Fallon - Market Center", fy2026Actual: 0, transactionCount2026: 0, avgTransaction2026: 0, itemsSold2026: 0, avgItemValue2026: 0, donationCount2026: 0, fy2025Actual: 0 },
  { name: 'Olathe - Strang Line', fy2026Actual: 685120.50, transactionCount2026: 30496, avgTransaction2026: 22.47, itemsSold2026: 167330, avgItemValue2026: 4.09, donationCount2026: 10382, fy2025Actual: 1178409.72 },
  { name: 'Outlet - Bannister', fy2026Actual: 1665380.63, transactionCount2026: 55153, avgTransaction2026: 30.20, itemsSold2026: 780995.57, avgItemValue2026: 2.13, donationCount2026: 6469, fy2025Actual: 1862014.71 },
  { name: 'Outlet - Market', fy2026Actual: 1881967.19, transactionCount2026: 80442, avgTransaction2026: 23.40, itemsSold2026: 1044470, avgItemValue2026: 1.80, donationCount2026: 401, fy2025Actual: 3561869.98 },
  { name: 'Outlet - Mills', fy2026Actual: 1731046.17, transactionCount2026: 71519, avgTransaction2026: 24.20, itemsSold2026: 1078577, avgItemValue2026: 1.60, donationCount2026: 2060, fy2025Actual: 3148230.28 },
  { name: 'Overland Park - 135th St', fy2026Actual: 1077181.02, transactionCount2026: 49263, avgTransaction2026: 21.87, itemsSold2026: 260687, avgItemValue2026: 4.13, donationCount2026: 30832, fy2025Actual: 2004323.67 },
  { name: 'Ozark - W South', fy2026Actual: 1313379.17, transactionCount2026: 58143, avgTransaction2026: 22.59, itemsSold2026: 274358, avgItemValue2026: 4.79, donationCount2026: 14364, fy2025Actual: 2584790.63 },
  { name: 'Pittsburg - Broadway', fy2026Actual: 704951.80, transactionCount2026: 32230, avgTransaction2026: 21.87, itemsSold2026: 163245, avgItemValue2026: 4.32, donationCount2026: 9294, fy2025Actual: 1288196.63 },
  { name: 'Poplar Bluff - Westwood', fy2026Actual: 1411983.43, transactionCount2026: 64088, avgTransaction2026: 22.03, itemsSold2026: 313063, avgItemValue2026: 4.51, donationCount2026: 8255, fy2025Actual: 2864489.03 },
  { name: 'Rolla - Forum', fy2026Actual: 1444286.59, transactionCount2026: 62474, avgTransaction2026: 23.12, itemsSold2026: 306544, avgItemValue2026: 4.71, donationCount2026: 13382, fy2025Actual: 2811233.15 },
  { name: 'Shawnee - Mission Pkwy', fy2026Actual: 1000620.32, transactionCount2026: 46325, avgTransaction2026: 21.60, itemsSold2026: 246549, avgItemValue2026: 4.06, donationCount2026: 17935, fy2025Actual: 1832496.36 },
  { name: 'Sikeston - Brunt', fy2026Actual: 1108321.07, transactionCount2026: 53977, avgTransaction2026: 20.53, itemsSold2026: 246791, avgItemValue2026: 4.49, donationCount2026: 5940, fy2025Actual: 2245273.69 },
  { name: 'South County - Baptist Church', fy2026Actual: 1934135.44, transactionCount2026: 90663, avgTransaction2026: 21.33, itemsSold2026: 383496, avgItemValue2026: 5.04, donationCount2026: 28684, fy2025Actual: 3713498.76 },
  { name: 'Springfield - Kansas Pkwy', fy2026Actual: 2002651.32, transactionCount2026: 91318, avgTransaction2026: 21.93, itemsSold2026: 429445, avgItemValue2026: 4.66, donationCount2026: 22903, fy2025Actual: 4023618.83 },
  { name: 'Springfield - Western Ave', fy2026Actual: 1323062.88, transactionCount2026: 60906, avgTransaction2026: 21.72, itemsSold2026: 284188, avgItemValue2026: 4.66, donationCount2026: 12267, fy2025Actual: 2524805.63 },
  { name: 'St. Charles - Clay', fy2026Actual: 1113568.23, transactionCount2026: 58757, avgTransaction2026: 18.95, itemsSold2026: 249029, avgItemValue2026: 4.47, donationCount2026: 14672, fy2025Actual: 2016361.43 },
  { name: 'St. Joseph - Faraon', fy2026Actual: 659692.50, transactionCount2026: 35419, avgTransaction2026: 18.63, itemsSold2026: 169132, avgItemValue2026: 3.90, donationCount2026: 11637, fy2025Actual: 1175797.49 },
  { name: 'St. Louis - Bayless', fy2026Actual: 929950.58, transactionCount2026: 50267, avgTransaction2026: 18.50, itemsSold2026: 199955, avgItemValue2026: 4.65, donationCount2026: 16063, fy2025Actual: 1653477.01 },
  { name: 'St. Peters - Harvester', fy2026Actual: 1130893.66, transactionCount2026: 60723, avgTransaction2026: 18.62, itemsSold2026: 255928, avgItemValue2026: 4.42, donationCount2026: 24061, fy2025Actual: 2172499.19 },
  { name: 'St. Peters - Mid Rivers', fy2026Actual: 1183374.72, transactionCount2026: 63247, avgTransaction2026: 18.71, itemsSold2026: 246477, avgItemValue2026: 4.80, donationCount2026: 24090, fy2025Actual: 2492664.87 },
  { name: 'St. Robert - Marshall', fy2026Actual: 1031634.11, transactionCount2026: 43979, avgTransaction2026: 23.46, itemsSold2026: 234754, avgItemValue2026: 4.39, donationCount2026: 6046, fy2025Actual: 1944257.84 },
  { name: 'Sunset Hills - Watson', fy2026Actual: 870862.77, transactionCount2026: 49242, avgTransaction2026: 17.69, itemsSold2026: 162583, avgItemValue2026: 5.36, donationCount2026: 23068, fy2025Actual: 1617878.83 },
  { name: 'Topeka - 21st St', fy2026Actual: 833158.75, transactionCount2026: 41887, avgTransaction2026: 19.89, itemsSold2026: 210689, avgItemValue2026: 3.95, donationCount2026: 21567, fy2025Actual: 2300465.26 },
  { name: 'University City - Olive', fy2026Actual: 1365211.02, transactionCount2026: 68668, avgTransaction2026: 19.88, itemsSold2026: 276427, avgItemValue2026: 4.94, donationCount2026: 29495, fy2025Actual: 2672549.60 },
  { name: 'Waldo - Wornall', fy2026Actual: 751177.52, transactionCount2026: 37907, avgTransaction2026: 19.82, itemsSold2026: 201037, avgItemValue2026: 3.74, donationCount2026: 9078, fy2025Actual: 1242530.93 },
  { name: 'Washington - Highway 100', fy2026Actual: 1317059.57, transactionCount2026: 61731, avgTransaction2026: 21.34, itemsSold2026: 282938, avgItemValue2026: 4.65, donationCount2026: 22855, fy2025Actual: 2618417.40 },
  { name: 'Wentzville - Wentzville Pkwy', fy2026Actual: 1584620.87, transactionCount2026: 68626, avgTransaction2026: 23.09, itemsSold2026: 344285, avgItemValue2026: 4.60, donationCount2026: 19948, fy2025Actual: 1118102.16 },
];

// Kansas / Kansas-City-metro market footprint = West region. Everything else (St. Louis metro,
// southern/central MO, southern IL) = East. Matches the ~$111.8M East / $45.3M West split in the
// FY2027 Databricks baseline scenario parameters.
const WEST_STORE_NAMES = new Set<string>([
  'ADC - Leawood',
  'Blue Springs - MO-7',
  'Bonner Springs - 129th St',
  'Kansas City - E 63rd',
  'Kansas City - Main St',
  'Kansas City - N Oak',
  'Lawrence - 31st St',
  'Leavenworth - Broadway',
  "Lee's Summit - Ward",
  'Liberty - N Cedar',
  'Manhattan - Poyntz',
  'Olathe - Strang Line',
  'Outlet - Bannister',
  'Overland Park - 135th St',
  'Pittsburg - Broadway',
  'Shawnee - Mission Pkwy',
  'St. Joseph - Faraon',
  'Topeka - 21st St',
  'Waldo - Wornall',
]);

function codeFromName(name: string, used: Set<string>): string {
  const cleaned = name.replace(/[^A-Za-z0-9\s]/g, '').toUpperCase();
  const words = cleaned.split(/\s+/).filter(Boolean);
  const primary = words[0] ?? 'STOR';
  const candidates = [
    primary.slice(0, 4),
    primary.slice(0, 3) + (words[1]?.[0] ?? 'X'),
    primary.slice(0, 2) + (words[1]?.slice(0, 2) ?? 'XX'),
  ];
  for (const candidate of candidates) {
    if (candidate.length >= 3 && !used.has(candidate)) {
      used.add(candidate);
      return candidate;
    }
  }
  let n = 1;
  let fallback = `${primary.slice(0, 3)}${n}`;
  while (used.has(fallback)) {
    n += 1;
    fallback = `${primary.slice(0, 3)}${n}`;
  }
  used.add(fallback);
  return fallback;
}

function escapeSheetNameForFormula(sheetName: string): string {
  return sheetName.replace(/'/g, "''");
}

// Real POS store codes, sourced from column C row 2 of each store tab in
// "2026 All months Goals - 07 - Daily Goals (By Store).xlsx". That workbook only covers 44 of the
// 67 stores here (no Kansas/West-region stores, no ADC donation centers) — a handful of its
// entries (Bridgeton Outlet, Fenton, O'Fallon MO/IL, Springfield Battlefield/Chestnut Crossing)
// don't map 1:1 to a single store in this list, so those are intentionally left ungenerated rather
// than guessed. Everything not in this map falls back to codeFromName()'s generated code.
const CODE_OVERRIDES: Record<string, string> = {
  'ADC - Leawood': 'LEAD',
  'Alton - Homer Adams': 'ALTS',
  'Arnold - Richardson': 'ARNS',
  'St. Louis - Bayless': 'BAYS',
  'Belleville - Belt': 'BELS',
  'Brentwood - Manchester': 'BRES',
  'Cape Girardeau - Silver Springs': 'CAPS',
  'Carbondale - Main': 'CARS',
  'Centralia - Broadway': 'CENS',
  'Chesterfield - Forum': 'CHES',
  'Chesterfield - Valley': 'CHVS',
  'Columbia - Grindstone': 'COLS',
  'Farmington - Potosi': 'FARS',
  'Fenton - Gravois Bluffs': 'FENS',
  'Festus - Truman': 'FESS',
  'Florissant - Highway 67': 'FLOS',
  'Glen Carbon - Junction': 'GLCS',
  'Granite City - Nameoki': 'GRAS',
  'Hannibal - Stardust': 'HANS',
  'St. Peters - Harvester': 'HARS',
  'Jefferson City - Ten Mile': 'JEFS',
  'Jennings - Florissant': 'JENS',
  'Lake St. Louis - Robert Raymond': 'LAKS',
  'Manchester - Manchester Rd': 'MANS',
  'Marion - Outer': 'MARS',
  'Mexico - Clark': 'MEXS',
  'Moberly - Morley': 'MOBS',
  'Outlet - Market': 'OUTS',
  'Ozark - W South': 'OZAS',
  'Poplar Bluff - Westwood': 'POPS',
  'Rolla - Forum': 'ROLS',
  'Sikeston - Brunt': 'SIKS',
  'South County - Baptist Church': 'SOUS',
  'St. Charles - Clay': 'STCS',
  'St. Peters - Mid Rivers': 'SPTS',
  'St. Robert - Marshall': 'STRS',
  'University City - Olive': 'UNIS',
  'Washington - Highway 100': 'WASS',
  'Sunset Hills - Watson': 'WATS',
  'Wentzville - Wentzville Pkwy': 'WENS',
};

function buildStores(): Store[] {
  const usedCodes = new Set<string>();
  return RAW_STORES.map((row) => {
    const code = CODE_OVERRIDES[row.name] ?? codeFromName(row.name, usedCodes);
    usedCodes.add(code);
    const status: StoreStatus =
      row.fy2026Actual === 0 && row.fy2025Actual === 0
        ? 'New store'
        : row.fy2026Actual === 0
          ? 'Closed'
          : 'Continuing';
    return {
      code,
      name: row.name,
      sheetName: `${code} - ${row.name}`,
      region: WEST_STORE_NAMES.has(row.name) ? 'West' : 'East',
      status,
      fy2025Actual: row.fy2025Actual,
      fy2026Actual: row.fy2026Actual,
      transactionCount2026: row.transactionCount2026,
      avgTransaction2026: row.avgTransaction2026,
      itemsSold2026: row.itemsSold2026,
      avgItemValue2026: row.avgItemValue2026,
      donationCount2026: row.donationCount2026,
    };
  });
}

export const STORES: Store[] = buildStores();

export const CONTINUING_STORES = STORES.filter((s) => s.status === 'Continuing');
export const NEW_STORES = STORES.filter((s) => s.status === 'New store');
export const CLOSED_STORES = STORES.filter((s) => s.status === 'Closed');

export function getStoreByCode(code: string): Store | undefined {
  return STORES.find((s) => s.code === code);
}

export function sheetFormulaRef(sheetName: string, cellRef: string): string {
  return `'${escapeSheetNameForFormula(sheetName)}'!${cellRef}`;
}

export { escapeSheetNameForFormula };
