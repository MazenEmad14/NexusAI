import React from 'react';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area,
  ScatterChart, Scatter, ZAxis,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Cell
} from 'recharts';

const COLORS = ['#00f0ff', '#a855f7', '#10b981', '#f59e0b', '#ef4444', '#3b82f6'];

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[#0a0a0a]/90 border border-white/10 p-3 rounded-lg shadow-xl backdrop-blur-md text-sm">
        <p className="text-white/60 mb-1">{label}</p>
        {payload.map((entry, index) => (
          <p key={`item-${index}`} className="font-semibold" style={{ color: entry.color }}>
            {entry.name}: {entry.value}
          </p>
        ))}
      </div>
    );
  }
  return null;
};

export default function ChartRenderer({ chartData }) {
  if (!chartData || !chartData.data || !chartData.type) return null;

  const { type, xAxisKey, data } = chartData;

  // Extract all keys from the first data object except the xAxisKey to determine what to plot
  const dataKeys = data.length > 0 
    ? Object.keys(data[0]).filter(k => k !== xAxisKey)
    : [];

  const renderChart = () => {
    switch (type.toLowerCase()) {
      case 'bar':
        return (
          <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#ffffff15" vertical={false} />
            <XAxis dataKey={xAxisKey} stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <YAxis stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: '12px', opacity: 0.8 }} />
            {dataKeys.map((key, i) => (
              <Bar key={key} dataKey={key} fill={COLORS[i % COLORS.length]} radius={[4, 4, 0, 0]} />
            ))}
          </BarChart>
        );
      case 'line':
        return (
          <LineChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#ffffff15" vertical={false} />
            <XAxis dataKey={xAxisKey} stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <YAxis stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: '12px', opacity: 0.8 }} />
            {dataKeys.map((key, i) => (
              <Line key={key} type="monotone" dataKey={key} stroke={COLORS[i % COLORS.length]} strokeWidth={3} dot={{r: 4, strokeWidth: 2}} activeDot={{r: 6}} />
            ))}
          </LineChart>
        );
      case 'area':
        return (
          <AreaChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
            <defs>
              {dataKeys.map((key, i) => (
                <linearGradient key={`color-${key}`} id={`color-${key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.3}/>
                  <stop offset="95%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0}/>
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#ffffff15" vertical={false} />
            <XAxis dataKey={xAxisKey} stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <YAxis stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: '12px', opacity: 0.8 }} />
            {dataKeys.map((key, i) => (
              <Area key={key} type="monotone" dataKey={key} stroke={COLORS[i % COLORS.length]} fillOpacity={1} fill={`url(#color-${key})`} />
            ))}
          </AreaChart>
        );
      case 'pie': {
        const pieDataKey = dataKeys[0] || 'value';
        return (
          <PieChart margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: '12px', opacity: 0.8 }} />
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={60}
              outerRadius={90}
              paddingAngle={5}
              dataKey={pieDataKey}
              nameKey={xAxisKey}
            >
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} stroke="rgba(255,255,255,0.1)" />
              ))}
            </Pie>
          </PieChart>
        );
      }
      case 'scatter': {
        const yKey = dataKeys[0] || 'value';
        const zKey = dataKeys.find(k => k !== yKey) || xAxisKey;
        return (
          <ScatterChart margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#ffffff15" />
            <XAxis dataKey={xAxisKey} name={xAxisKey} type="number" stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <YAxis dataKey={yKey} name={yKey} type="number" stroke="#ffffff40" tick={{fill: '#ffffff60', fontSize: 12}} />
            <ZAxis dataKey={zKey} name={zKey} range={[60, 60]} />
            <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3' }} />
            <Legend wrapperStyle={{ fontSize: '12px', opacity: 0.8 }} />
            <Scatter name="Data" data={data} fill={COLORS[0]} />
          </ScatterChart>
        );
      }
      default:
        return (
          <div className="text-white/40 text-sm flex items-center justify-center h-full">
            Unsupported chart type: {type}
          </div>
        );
    }
  };

  return (
    <div className="mt-4 p-4 rounded-xl bg-white/[0.02] border border-white/5 shadow-2xl h-[350px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        {renderChart()}
      </ResponsiveContainer>
    </div>
  );
}
