import { Component, type ReactNode } from 'react';
import ReactECharts from 'echarts-for-react';
import type { ChartSpec } from '../../api/client';
import { buildOption } from './chartOptions';

interface ChartRendererProps {
  spec: ChartSpec;
  rows: Record<string, string>[];
  size: 'thumb' | 'full';
}

class ChartErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="py-6 text-center text-xs text-splunk-text-muted border border-dashed border-splunk-border rounded">
          Chart unavailable — view table
        </div>
      );
    }
    return this.props.children;
  }
}

export function ChartRenderer({ spec, rows, size }: ChartRendererProps) {
  const height = size === 'thumb' ? 140 : 360;
  const option = buildOption(spec, rows, size);
  return (
    <ChartErrorBoundary>
      <ReactECharts
        option={option}
        style={{ height, width: '100%' }}
        opts={{ renderer: 'svg' }}
        notMerge
      />
    </ChartErrorBoundary>
  );
}
