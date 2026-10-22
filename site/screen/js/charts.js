/* 大屏图表模块（ECharts 5 · 本地 vendor）
   所有实例按 1920×1080 设计稿尺寸初始化，随 #stage 一起被 CSS transform 缩放 */
window.ScreenCharts = (function () {
  'use strict';

  var FONT = 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif';
  var C = {
    primary: '#00467f',
    blue: '#2a6fb5',
    blueLight: '#5b9bd5',
    teal: '#0f766e',
    green: '#16a34a',
    red: '#e60012',
    gray: '#94a3b8',
    muted: '#5b6475',
    line: '#d8e0ea'
  };
  // 区县(项目库值) → geojson 要素名
  var MAP_NAME = {
    '亭湖': '亭湖区', '盐都': '盐都区', '大丰': '大丰区', '东台': '东台市',
    '建湖': '建湖县', '射阳': '射阳县', '阜宁': '阜宁县', '滨海': '滨海县', '响水': '响水县'
  };
  // 功能区/市本级标签的近似坐标（仅作标注，不代表行政区划）
  var BADGES = [
    { name: '市本级', coord: [120.152, 33.402], color: C.primary },
    { name: '盐南', coord: [120.112, 33.305], color: C.teal },
    { name: '经开', coord: [120.235, 33.362], color: C.warn }
  ];

  var charts = {};

  function baseText(size, weight) {
    return { fontFamily: FONT, fontSize: size || 14, fontWeight: weight || 400, color: C.muted };
  }

  function hBarOption(items, color, opts) {
    opts = opts || {};
    var data = items.map(function (x) {
      return {
        value: x.count != null ? x.count : x.projects,
        itemStyle: x.name === '未识别' ? { color: C.gray } : { color: color }
      };
    });
    return {
      textStyle: baseText(13),
      tooltip: { show: false },
      grid: { left: 8, right: 56, top: 6, bottom: 4, containLabel: true },
      xAxis: { type: 'value', show: false },
      yAxis: {
        type: 'category', inverse: true,
        data: items.map(function (x) { return x.name; }),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { textStyle: baseText(14.5, 600), color: '#172033' }
      },
      series: [{
        type: 'bar',
        data: data,
        barWidth: '62%',
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: {
          show: true, position: 'right',
          textStyle: baseText(13.5, 700), color: '#172033'
        }
      }]
    };
  }

  function init(geojson) {
    echarts.registerMap('yancheng', geojson);

    charts.district = echarts.init(document.getElementById('chart-district'));
    charts.industry = echarts.init(document.getElementById('chart-industry'));
    charts.map = echarts.init(document.getElementById('chart-map'));
    charts.trend = echarts.init(document.getElementById('chart-trend'));
    charts.stage = echarts.init(document.getElementById('chart-stage'));
  }

  function update(data) {
    /* ---- 区县排行（未识别已在导出端钉底） ---- */
    charts.district.setOption(hBarOption(data.districts, C.blue));

    /* ---- 行业 Top8 ---- */
    charts.industry.setOption(hBarOption(data.industries.top, C.teal));

    /* ---- 盐城地图 ---- */
    var mapData = [];
    data.districts.forEach(function (x) {
      var mapped = MAP_NAME[x.name];
      if (mapped) mapData.push({ name: mapped, value: x.projects });
    });
    var maxV = mapData.reduce(function (m, x) { return Math.max(m, x.value); }, 0);
    var badgeData = BADGES.map(function (b) {
      var hit = null;
      data.districts.forEach(function (x) { if (x.name === b.name) hit = x; });
      return {
        name: b.name,
        value: b.coord.concat(hit ? hit.projects : 0),
        itemStyle: { color: b.color }
      };
    });
    charts.map.setOption({
      textStyle: baseText(13),
      tooltip: {
        trigger: 'item',
        formatter: function (p) {
          if (p.seriesType === 'scatter') return p.name + '：' + p.value[2] + ' 个项目';
          return p.name + '：' + (p.value || 0) + ' 个项目';
        }
      },
      visualMap: {
        min: 0, max: maxV || 1,
        left: 10, bottom: 8, itemHeight: 110, text: ['多', '少'],
        textStyle: baseText(12),
        inRange: { color: ['#e3edf7', '#a8c8e8', '#5b9bd5', '#2a6fb5', '#00467f'] }
      },
      geo: {
        map: 'yancheng', roam: false,
        layoutCenter: ['50%', '46%'], layoutSize: '92%',
        label: { show: true, textStyle: baseText(12.5, 600), color: '#172033' },
        itemStyle: { borderColor: '#ffffff', borderWidth: 1.4 },
        emphasis: { label: { show: true }, itemStyle: { areaColor: '#f6c65b' } }
      },
      series: [
        {
          name: '项目分布', type: 'map', geoIndex: 0,
          data: mapData
        },
        {
          name: '功能区/市本级', type: 'scatter', coordinateSystem: 'geo',
          data: badgeData,
          symbolSize: 13,
          label: {
            show: true,
            formatter: function (p) { return p.name + ' ' + p.value[2]; },
            position: 'right',
            textStyle: baseText(13, 800), color: '#172033',
            backgroundColor: 'rgba(255,255,255,0.85)',
            padding: [3, 5], borderRadius: 4
          },
          itemStyle: { borderColor: '#fff', borderWidth: 1.5 },
          zlevel: 2
        }
      ]
    });

    /* ---- 30 天趋势 ---- */
    var days = data.trend_30d.map(function (x) { return x.date.slice(5); });
    charts.trend.setOption({
      textStyle: baseText(12),
      tooltip: { trigger: 'axis' },
      legend: {
        top: 2, right: 6, itemWidth: 14, itemHeight: 8,
        textStyle: baseText(12.5), data: ['新增项目', '中标公告']
      },
      grid: { left: 8, right: 12, top: 30, bottom: 2, containLabel: true },
      xAxis: {
        type: 'category', boundaryGap: false, data: days,
        axisLine: { lineStyle: { color: C.line } },
        axisTick: { show: false },
        axisLabel: { textStyle: baseText(11.5), interval: 4 }
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#eef2f7' } },
        axisLabel: { textStyle: baseText(11.5) }
      },
      series: [
        {
          name: '新增项目', type: 'line', smooth: true, symbol: 'none',
          data: data.trend_30d.map(function (x) { return x.new_projects; }),
          lineStyle: { width: 2.5, color: C.primary },
          itemStyle: { color: C.primary },
          areaStyle: { color: 'rgba(0,70,127,0.10)' }
        },
        {
          name: '中标公告', type: 'line', smooth: true, symbol: 'none',
          data: data.trend_30d.map(function (x) { return x.award_notices; }),
          lineStyle: { width: 2, color: C.green },
          itemStyle: { color: C.green }
        }
      ]
    });

    /* ---- 项目阶段分布 ---- */
    var stageColors = [C.teal, C.blue, C.blueLight, C.green, C.gray];
    charts.stage.setOption({
      textStyle: baseText(13),
      tooltip: { show: false },
      grid: { left: 8, right: 56, top: 4, bottom: 2, containLabel: true },
      xAxis: { type: 'value', show: false },
      yAxis: {
        type: 'category', inverse: true,
        data: data.stages.map(function (x) { return x.name; }),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { textStyle: baseText(13.5, 600), color: '#172033' }
      },
      series: [{
        type: 'bar', barWidth: '58%',
        data: data.stages.map(function (x, i) {
          return { value: x.count, itemStyle: { color: stageColors[i % stageColors.length], borderRadius: [0, 4, 4, 0] } };
        }),
        label: { show: true, position: 'right', textStyle: baseText(13, 700), color: '#172033' }
      }]
    });
  }

  return { init: init, update: update };
})();
