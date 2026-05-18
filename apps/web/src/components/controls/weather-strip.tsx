"use client";

import { Cloud, CloudRain, CloudSnow, Sun, Wind, CloudSun } from "lucide-react";

interface WeatherDay {
  date: string;
  temperature_high: number;
  temperature_low: number;
  conditions: string;
  precipitation_inches: number;
  wind_mph: number;
  impact_score?: number;
}

interface WeatherStripProps {
  forecast: WeatherDay[];
}

const weatherIcon = (condition: string) => {
  switch (condition.toLowerCase()) {
    case "rain":
      return <CloudRain className="h-5 w-5 text-blue-500" />;
    case "snow":
      return <CloudSnow className="h-5 w-5 text-blue-300" />;
    case "overcast":
      return <Cloud className="h-5 w-5 text-gray-400" />;
    case "partly_cloudy":
      return <CloudSun className="h-5 w-5 text-amber-400" />;
    case "windy":
      return <Wind className="h-5 w-5 text-teal-500" />;
    default:
      return <Sun className="h-5 w-5 text-yellow-500" />;
  }
};

const impactBorder = (score?: number) => {
  if (!score || score >= 80) return "border-green-200";
  if (score >= 50) return "border-amber-300";
  return "border-red-300";
};

export function WeatherStrip({ forecast }: WeatherStripProps) {
  if (!forecast.length) {
    return (
      <div className="text-sm text-gray-400 text-center py-4">No weather forecast available</div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-900 mb-3">7-Day Weather Forecast</h3>
      <div className="grid grid-cols-7 gap-2">
        {forecast.slice(0, 7).map((day) => {
          const d = new Date(day.date);
          const dayName = d.toLocaleDateString("en-US", { weekday: "short" });
          return (
            <div
              key={day.date}
              className={`flex flex-col items-center gap-1 rounded-lg border-2 ${impactBorder(day.impact_score)} p-2`}
            >
              <span className="text-xs font-medium text-gray-600">{dayName}</span>
              {weatherIcon(day.conditions)}
              <div className="text-xs text-center">
                <span className="font-medium">{Math.round(day.temperature_high)}°</span>
                <span className="text-gray-400"> / {Math.round(day.temperature_low)}°</span>
              </div>
              {day.wind_mph > 20 && (
                <span className="text-[10px] text-amber-600">{Math.round(day.wind_mph)} mph</span>
              )}
              {day.precipitation_inches > 0.1 && (
                <span className="text-[10px] text-blue-500">
                  {day.precipitation_inches.toFixed(1)}&quot;
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
