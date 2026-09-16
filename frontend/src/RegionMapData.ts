export type RegionMapSource = {
  sourceName: string;
  code: string;
  name: string;
};

// The source uses its 2020 names for Gangwon and Jeollabuk; the UI maps them
// to the current administrative names and the API's 10-digit region codes.
export const regionMapSources: RegionMapSource[] = [
  { sourceName: '서울특별시', code: '1100000000', name: '서울특별시' },
  { sourceName: '부산광역시', code: '2600000000', name: '부산광역시' },
  { sourceName: '대구광역시', code: '2700000000', name: '대구광역시' },
  { sourceName: '인천광역시', code: '2800000000', name: '인천광역시' },
  { sourceName: '광주광역시', code: '2900000000', name: '광주광역시' },
  { sourceName: '대전광역시', code: '3000000000', name: '대전광역시' },
  { sourceName: '울산광역시', code: '3100000000', name: '울산광역시' },
  { sourceName: '세종특별자치시', code: '3600000000', name: '세종특별자치시' },
  { sourceName: '경기도', code: '4100000000', name: '경기도' },
  { sourceName: '강원도', code: '5100000000', name: '강원특별자치도' },
  { sourceName: '충청북도', code: '4300000000', name: '충청북도' },
  { sourceName: '충청남도', code: '4400000000', name: '충청남도' },
  { sourceName: '전라북도', code: '5200000000', name: '전북특별자치도' },
  { sourceName: '전라남도', code: '4600000000', name: '전라남도' },
  { sourceName: '경상북도', code: '4700000000', name: '경상북도' },
  { sourceName: '경상남도', code: '4800000000', name: '경상남도' },
  { sourceName: '제주특별자치도', code: '5000000000', name: '제주특별자치도' },
];
