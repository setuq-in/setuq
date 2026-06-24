import type { ChartSpec, ChartType } from '../../api/client';

export const CHART_TYPES: ChartType[] = [
  'line', 'area', 'bar', 'column', 'stacked_bar', 'pie',
  'scatter', 'bubble', 'single_value', 'gauge', 'heatmap',
];

type Row = Record<string, string>;

const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

const AXIS_DARK = {
  axisLine: { lineStyle: { color: '#3a3d44' } },
  axisLabel: { color: '#9aa0a6', fontSize: 10 },
  splitLine: { lineStyle: { color: '#26282d' } },
};

const BASE_COLORS = ['#5cc8c8', '#4f8ff7', '#f8be34', '#dc4e41', '#a972f5', '#53a051'];

/**
 * Build an ECharts option from a ChartSpec + result rows.
 * Mirrors the chart types produced by the backend chart_inferer.
 */
export function buildOption(spec: ChartSpec, rows: Row[], size: 'thumb' | 'full'): Record<string, unknown> {
  const thumb = size === 'thumb';
  const x = spec.x_field;
  const ys = spec.y_fields;
  const common = {
    color: BASE_COLORS,
    backgroundColor: 'transparent',
    grid: { left: thumb ? 8 : 48, right: thumb ? 8 : 24, top: thumb ? 12 : 40, bottom: thumb ? 8 : 40, containLabel: true },
    tooltip: thumb ? { show: false } : { trigger: 'axis' },
    legend: thumb || ys.length <= 1 ? { show: false } : { textStyle: { color: '#9aa0a6' }, top: 4 },
  };

  switch (spec.chart_type) {
    case 'line':
    case 'area': {
      return {
        ...common,
        xAxis: { type: 'category', data: rows.map((r) => (x ? r[x] : '')), ...AXIS_DARK },
        yAxis: { type: 'value', ...AXIS_DARK },
        series: ys.map((y) => ({
          name: y,
          type: 'line',
          smooth: true,
          showSymbol: !thumb,
          areaStyle: spec.chart_type === 'area' ? {} : undefined,
          data: rows.map((r) => num(r[y])),
        })),
      };
    }
    case 'bar':
    case 'column':
    case 'stacked_bar': {
      const horizontal = spec.chart_type === 'bar';
      const cat = { type: 'category', data: rows.map((r) => (x ? r[x] : '')), ...AXIS_DARK };
      const val = { type: 'value', ...AXIS_DARK };
      return {
        ...common,
        xAxis: horizontal ? val : cat,
        yAxis: horizontal ? cat : val,
        series: ys.map((y) => ({
          name: y,
          type: 'bar',
          stack: spec.chart_type === 'stacked_bar' ? 'total' : undefined,
          data: rows.map((r) => num(r[y])),
        })),
      };
    }
    case 'pie': {
      return {
        ...common,
        tooltip: thumb ? { show: false } : { trigger: 'item' },
        series: [
          {
            type: 'pie',
            radius: thumb ? '70%' : ['40%', '70%'],
            label: { show: !thumb, color: '#9aa0a6' },
            data: rows.map((r) => ({ name: x ? r[x] : '', value: num(r[ys[0]]) })),
          },
        ],
      };
    }
    case 'scatter':
    case 'bubble': {
      const xf = x ?? ys[0];
      const yf = ys[0];
      const sizeField = spec.chart_type === 'bubble' ? ys[1] : null;
      return {
        ...common,
        xAxis: { type: 'value', name: thumb ? '' : xf ?? '', ...AXIS_DARK },
        yAxis: { type: 'value', name: thumb ? '' : yf ?? '', ...AXIS_DARK },
        series: [
          {
            type: 'scatter',
            symbolSize: sizeField
              ? (d: number[]) => Math.max(6, Math.sqrt(d[2]) )
              : thumb ? 5 : 10,
            data: rows.map((r) => (sizeField ? [num(r[xf]), num(r[yf]), num(r[sizeField])] : [num(r[xf]), num(r[yf])])),
          },
        ],
      };
    }
    case 'single_value': {
      const value = rows.length ? rows[0][ys[0]] : '—';
      return {
        backgroundColor: 'transparent',
        graphic: {
          type: 'text',
          left: 'center',
          top: 'middle',
          style: { text: String(value), fontSize: thumb ? 28 : 56, fontWeight: 'bold', fill: '#5cc8c8' },
        },
      };
    }
    case 'gauge': {
      const value = rows.length ? num(rows[0][ys[0]]) : 0;
      return {
        backgroundColor: 'transparent',
        series: [
          {
            type: 'gauge',
            progress: { show: true },
            detail: { valueAnimation: true, color: '#5cc8c8', fontSize: thumb ? 12 : 20 },
            data: [{ value }],
          },
        ],
      };
    }
    case 'heatmap': {
      const xf = x ?? '';
      const yf = spec.series_field ?? '';
      const vf = ys[0];
      const xCats = [...new Set(rows.map((r) => r[xf]))];
      const yCats = [...new Set(rows.map((r) => r[yf]))];
      const values = rows.map((r) => num(r[vf]));
      return {
        backgroundColor: 'transparent',
        tooltip: { position: 'top' },
        grid: { left: 60, right: 24, top: 16, bottom: 40, containLabel: true },
        xAxis: { type: 'category', data: xCats, ...AXIS_DARK },
        yAxis: { type: 'category', data: yCats, ...AXIS_DARK },
        visualMap: {
          min: Math.min(0, ...values),
          max: Math.max(1, ...values),
          calculable: true,
          orient: 'horizontal',
          left: 'center',
          bottom: 0,
          show: !thumb,
          inRange: { color: ['#1a2332', '#4f8ff7', '#dc4e41'] },
          textStyle: { color: '#9aa0a6' },
        },
        series: [
          {
            type: 'heatmap',
            data: rows.map((r) => [xCats.indexOf(r[xf]), yCats.indexOf(r[yf]), num(r[vf])]),
          },
        ],
      };
    }
    default:
      return {};
  }
}
