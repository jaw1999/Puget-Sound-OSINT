"""
Web UI and API for Puget Sound OSINT Platform.

Provides configuration interface for:
- Callsign and reporting settings
- TAI code mappings
- Camera feed management
- ChatSurfer integration
- Detection settings
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
import cv2
import io
from fastapi.staticfiles import StaticFiles
import uvicorn

from ..tracking.wsf_api import WSFVesselsClient, VesselTracker

if TYPE_CHECKING:
    from ..app import PugetSoundOSINT

logger = logging.getLogger(__name__)

# HTML Template for configuration UI
CONFIG_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Puget Sound OSINT</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
    <style>
        :root {
            --bg: #0a0e14;
            --card: #12171f;
            --card-hover: #1a2130;
            --border: #252d3a;
            --text: #e1e4e8;
            --text-dim: #6e7a8a;
            --accent: #4d9fff;
            --success: #2dd4a0;
            --warning: #f0b429;
            --danger: #ff5c5c;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
            padding: 24px 48px;
            max-width: 100%;
            margin: 0;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        h1 { font-size: 18px; font-weight: 600; letter-spacing: -0.3px; }
        .status-badge {
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .status-badge::before {
            content: '';
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: currentColor;
        }
        .status-badge.ok { background: rgba(45, 212, 160, 0.15); color: var(--success); }
        .status-badge.warn { background: rgba(240, 180, 41, 0.15); color: var(--warning); }
        .status-badge.error { background: rgba(255, 92, 92, 0.15); color: var(--danger); }

        .status-bar {
            display: flex;
            gap: 16px;
            align-items: center;
            margin-bottom: 24px;
            padding: 16px 20px;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 10px;
            flex-wrap: wrap;
        }
        .status-item {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
        }
        .status-item .label { color: var(--text-dim); }
        .status-item .value { font-weight: 600; font-variant-numeric: tabular-nums; }
        .status-item .value.ok { color: var(--success); }
        .status-item .value.warn { color: var(--warning); }
        .status-item .value.error { color: var(--danger); }
        .status-sep { width: 1px; height: 20px; background: var(--border); }

        .control-bar {
            display: flex;
            gap: 10px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }

        .tabs {
            display: flex;
            gap: 4px;
            margin-bottom: 16px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 0;
            flex-wrap: wrap;
        }
        .tab {
            padding: 10px 18px;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-dim);
            background: none;
            border: none;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            margin-bottom: -1px;
            transition: color 0.15s;
        }
        .tab:hover { color: var(--text); }
        .tab.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 10px;
            margin-bottom: 16px;
        }
        .card-header {
            padding: 14px 18px;
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            font-size: 13px;
            color: var(--text);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .card-body { padding: 18px; }

        .form-row {
            display: flex;
            align-items: center;
            margin-bottom: 14px;
        }
        .form-row:last-child { margin-bottom: 0; }
        .form-label {
            flex: 1;
            font-size: 13px;
            color: var(--text-dim);
        }
        .form-input {
            width: 240px;
            padding: 10px 14px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text);
            font-size: 13px;
            transition: border-color 0.15s, box-shadow 0.15s;
        }
        .form-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(77, 159, 255, 0.12);
        }
        .form-input.wide { width: 400px; }

        .btn-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 20px; }
        .btn {
            padding: 11px 18px;
            border: none;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
        }
        .btn:hover { filter: brightness(1.1); transform: translateY(-1px); }
        .btn:active { transform: translateY(0) scale(0.98); }
        .btn-primary { background: var(--accent); color: #fff; }
        .btn-success { background: var(--success); color: #0a0e14; }
        .btn-danger { background: var(--danger); color: #fff; }
        .btn-outline {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text);
        }
        .btn-outline:hover { background: var(--card-hover); border-color: var(--text-dim); }

        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            padding: 14px 22px;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 500;
            opacity: 0;
            transform: translateY(20px);
            transition: all 0.3s;
            z-index: 1000;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }
        .toast.show { opacity: 1; transform: translateY(0); }
        .toast.success { background: var(--success); color: #0a0e14; }
        .toast.error { background: var(--danger); color: #fff; }

        .section-title {
            font-size: 10px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 14px;
            font-weight: 600;
        }
        .divider { height: 1px; background: var(--border); margin: 18px 0; }

        .camera-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 12px;
        }
        .camera-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .camera-item:hover { border-color: var(--accent); }
        .camera-item input { accent-color: var(--accent); }
        .camera-item.online { border-left: 3px solid var(--success); }
        .camera-item.offline { border-left: 3px solid var(--danger); opacity: 0.6; }
        .camera-item span { font-size: 12px; }
        .camera-tai {
            font-size: 10px;
            background: var(--accent);
            color: #fff;
            padding: 2px 6px;
            border-radius: 4px;
            margin-left: auto;
        }

        /* Live Feeds Grid */
        .feeds-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
        }
        .feed-card {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            transition: all 0.2s;
        }
        .feed-card:hover {
            border-color: var(--accent);
            transform: translateY(-2px);
        }
        .feed-card.offline {
            opacity: 0.5;
        }
        .feed-card.disabled {
            opacity: 0.3;
        }
        .feed-image-container {
            position: relative;
            width: 100%;
            padding-top: 56.25%; /* 16:9 aspect ratio */
            background: #000;
        }
        .detection-overlay {
            position: absolute;
            top: 8px;
            right: 8px;
            z-index: 10;
        }
        .detection-badge {
            background: rgba(45, 212, 160, 0.9);
            color: #000;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        .detection-badge::before {
            content: '🎯';
            font-size: 12px;
        }
        .map-ctrl-btn.loading {
            opacity: 0.5;
            pointer-events: none;
        }
        .map-ctrl-btn.loading::after {
            content: '';
            position: absolute;
            width: 16px;
            height: 16px;
            border: 2px solid transparent;
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .feed-image {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
        .feed-placeholder {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: var(--text-dim);
            font-size: 12px;
        }
        .feed-info {
            padding: 10px 12px;
        }
        .feed-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 4px;
        }
        .feed-name {
            font-weight: 600;
            font-size: 13px;
        }
        .feed-status {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--danger);
        }
        .feed-status.online { background: var(--success); }
        .feed-meta {
            font-size: 11px;
            color: var(--text-dim);
        }
        .feed-controls {
            display: flex;
            gap: 8px;
            margin-top: 8px;
        }
        .feeds-toolbar {
            display: flex;
            gap: 12px;
            align-items: center;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }
        .refresh-indicator {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: var(--text-dim);
        }
        .refresh-indicator.active { color: var(--success); }

        .tacrep-output {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px;
            font-family: monospace;
            font-size: 12px;
            max-height: 300px;
            overflow-y: auto;
        }
        .tacrep-entry {
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
            display: flex;
            gap: 10px;
            align-items: flex-start;
            font-size: 12px;
        }
        .tacrep-entry:last-child { border-bottom: none; }
        .tacrep-entry:hover { background: rgba(77,159,255,0.05); }
        .tacrep-time { color: var(--text-dim); flex-shrink: 0; font-family: monospace; font-size: 11px; min-width: 70px; }
        .tacrep-msg { flex: 1; word-break: break-all; color: var(--success); font-family: monospace; }
        .tacrep-source { color: var(--accent); font-size: 10px; padding: 1px 6px; border-radius: 8px; background: rgba(77,159,255,0.1); white-space: nowrap; }
        .tacrep-source.visual { color: #d29922; background: rgba(210,153,34,0.1); }
        .tacrep-source.api { color: #a371f7; background: rgba(163,113,247,0.1); }
        .tacrep-source.manual { color: var(--text-dim); background: rgba(110,122,138,0.1); }
        .tacrep-img { color: var(--accent); font-size: 11px; }

        /* Vessel markers */
        .vessel-marker {
            background: #1e3a5f;
            border: 2px solid #4d9fff;
            border-radius: 50%;
            width: 24px;
            height: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            color: #fff;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
            cursor: pointer;
            transition: transform 0.2s;
        }
        .vessel-marker:hover {
            transform: scale(1.2);
        }
        .vessel-marker.at-dock {
            border-color: var(--success);
            background: #1a3d2e;
        }
        .vessel-marker.underway {
            border-color: var(--warning);
            background: #3d3a1a;
        }
        .vessel-popup {
            font-size: 12px;
        }
        .vessel-popup h4 {
            margin: 0 0 8px;
            color: var(--accent);
        }
        .vessel-popup .info-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 4px;
        }
        .vessel-popup .label { color: #888; }
        .vessel-popup .value { font-weight: 600; }
        .vessel-legend {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 14px;
            font-size: 11px;
            margin-bottom: 16px;
        }
        .vessel-legend-item {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }
        .vessel-legend-item:last-child { margin-bottom: 0; }
        .vessel-legend-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            border: 2px solid;
        }
        .vessel-stats {
            display: flex;
            gap: 16px;
            margin-bottom: 12px;
            font-size: 12px;
        }
        .vessel-stat {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .vessel-stat .count {
            font-weight: 700;
            font-size: 16px;
        }

        @media (max-width: 600px) {
            .form-input { width: 140px; }
            .form-input.wide { width: 200px; }
        }

        /* Enhanced Map Styles */
        .map-container { position: relative; }
        .map-overlay { pointer-events: auto; }

        .map-ctrl-btn {
            width: 40px;
            height: 40px;
            background: rgba(22,27,34,0.95);
            backdrop-filter: blur(8px);
            border: 1px solid #30363d;
            border-radius: 8px;
            color: #8b949e;
            font-size: 18px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.15s;
        }
        .map-ctrl-btn:hover { background: #262c36; color: #e6edf3; }
        .map-ctrl-btn.active { background: #238636; border-color: #238636; color: #fff; }

        .map-btn {
            padding: 8px 14px;
            background: #238636;
            border: none;
            border-radius: 6px;
            color: #fff;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
        }
        .map-btn:hover { background: #2ea043; }
        .map-btn.secondary { background: #6e7681; }
        .map-btn.secondary:hover { background: #8b949e; }
        .map-btn.drawing { background: #d29922; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }

        /* Dark Leaflet theme */
        .leaflet-container { background: #0d1117; font-family: inherit; }
        .leaflet-popup-content-wrapper {
            background: #1c2128;
            color: #e6edf3;
            border-radius: 10px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
            max-width: 400px;
        }
        .leaflet-popup-tip { background: #1c2128; }
        .leaflet-popup-content { margin: 14px 16px; max-width: 380px; }
        .leaflet-popup-content img { border-radius: 6px; }
        .leaflet-popup-close-button {
            color: #8b949e !important;
            font-size: 20px !important;
            padding: 6px 8px !important;
        }
        .leaflet-popup-close-button:hover { color: #e6edf3 !important; }
        .leaflet-control-zoom a {
            background: #1c2128 !important;
            color: #e6edf3 !important;
            border-color: #30363d !important;
        }
        .leaflet-control-zoom a:hover { background: #262c36 !important; }
        .leaflet-control-attribution {
            background: rgba(28, 33, 40, 0.9) !important;
            color: #6e7681 !important;
        }
        .leaflet-control-attribution a { color: #58a6ff !important; }
        .leaflet-tooltip {
            background: rgba(28, 33, 40, 0.95);
            color: #e6edf3;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 11px;
            font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        }
        .leaflet-tooltip-left:before, .leaflet-tooltip-right:before,
        .leaflet-tooltip-top:before, .leaflet-tooltip-bottom:before {
            border-color: transparent;
        }

        /* Vessel list items */
        .vessel-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            background: #161b22;
            border-radius: 8px;
            margin-bottom: 6px;
            cursor: pointer;
            transition: all 0.15s;
            border: 1px solid transparent;
        }
        .vessel-item:hover { background: #1c2128; border-color: #30363d; }
        .vessel-item-icon { font-size: 18px; width: 28px; text-align: center; }
        .vessel-item-info { flex: 1; min-width: 0; }
        .vessel-item-name { font-size: 12px; font-weight: 600; color: #e6edf3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .vessel-item-route { font-size: 10px; color: #6e7681; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .vessel-item-speed { font-size: 11px; font-weight: 600; color: #8b949e; }

        /* Fullscreen map */
        .map-fullscreen {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            right: 0 !important;
            bottom: 0 !important;
            z-index: 9999 !important;
            border-radius: 0 !important;
            height: 100vh !important;
            max-width: none !important;
        }

        /* Feed lightbox */
        .feed-lightbox {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            z-index: 10000;
            background: rgba(0,0,0,0.92);
            align-items: center;
            justify-content: center;
            flex-direction: column;
            cursor: pointer;
        }
        .feed-lightbox.open { display: flex; }
        .feed-lightbox img {
            max-width: 92vw;
            max-height: 80vh;
            border-radius: 6px;
            box-shadow: 0 8px 40px rgba(0,0,0,0.6);
            cursor: default;
        }
        .feed-lightbox-header {
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 10px 20px;
            margin-bottom: 8px;
            color: #e6edf3;
            font-size: 15px;
        }
        .feed-lightbox-header .feed-name { font-weight: 600; font-size: 17px; }
        .feed-lightbox-header .camera-tai { background: var(--accent); color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .feed-lightbox-close {
            position: absolute;
            top: 16px; right: 24px;
            color: #8b949e;
            font-size: 28px;
            cursor: pointer;
            background: none; border: none;
            padding: 4px 10px;
            border-radius: 6px;
        }
        .feed-lightbox-close:hover { color: #fff; background: rgba(255,255,255,0.1); }
        .feed-lightbox-nav {
            display: flex;
            gap: 12px;
            margin-top: 12px;
        }
        .feed-lightbox-nav button {
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.15);
            color: #e6edf3;
            padding: 8px 18px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
        }
        .feed-lightbox-nav button:hover { background: rgba(255,255,255,0.2); }
        .feed-image-container { cursor: pointer; }
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"/>
                <path d="M12 2a10 10 0 0 1 0 20"/>
                <path d="M2 12h20"/>
                <path d="M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10"/>
            </svg>
            <h1>Puget Sound OSINT</h1>
        </div>
        <span class="status-badge" id="conn-badge">--</span>
    </header>

    <div class="status-bar">
        <div class="status-item">
            <span class="label">Callsign:</span>
            <span class="value" id="st-callsign">--</span>
        </div>
        <div class="status-sep"></div>
        <div class="status-item">
            <span class="label">Cameras:</span>
            <span class="value" id="st-cameras">0/0</span>
        </div>
        <div class="status-sep"></div>
        <div class="status-item">
            <span class="label">Reports:</span>
            <span class="value" id="st-reports">0</span>
        </div>
        <div class="status-sep"></div>
        <div class="status-item">
            <span class="label">Last TACREP:</span>
            <span class="value" id="st-last-report">--</span>
        </div>
        <div class="status-sep"></div>
        <div class="status-item">
            <span class="label">Detection:</span>
            <span class="value" id="st-detection" data-detection-count>OFF</span>
        </div>
    </div>

    <div class="control-bar">
        <button class="btn btn-success" id="btn-checkin" onclick="checkIn()">Check In</button>
        <button class="btn btn-danger" id="btn-checkout" onclick="checkOut()">Check Out</button>
        <button class="btn btn-outline" onclick="testReport()">Test Report</button>
    </div>

    <div class="tabs">
        <button class="tab active" onclick="switchTab('feeds')">Live Feeds</button>
        <button class="tab" onclick="switchTab('reporting')">Reporting</button>
        <button class="tab" onclick="switchTab('map')">TAI Map</button>
        <button class="tab" onclick="switchTab('cameras')">Cameras</button>
        <button class="tab" onclick="switchTab('tai')">TAI Codes</button>
        <button class="tab" onclick="switchTab('chatsurfer')">ChatSurfer</button>
        <button class="tab" onclick="switchTab('output')">Live Output</button>
    </div>

    <div id="tab-feeds" class="tab-content active">
        <div class="card">
            <div class="card-header" style="display: flex; justify-content: space-between; align-items: center;">
                <span>Camera Feeds</span>
                <div class="feeds-toolbar">
                    <select class="form-input" id="feeds-filter" style="width: 140px;" onchange="filterFeeds()">
                        <option value="all">All Cameras</option>
                        <option value="online">Online Only</option>
                        <option value="enabled">Enabled Only</option>
                    </select>
                    <select class="form-input" id="refresh-rate" style="width: 100px;" onchange="setRefreshRate()">
                        <option value="2000">2 sec</option>
                        <option value="5000" selected>5 sec</option>
                        <option value="10000">10 sec</option>
                        <option value="30000">30 sec</option>
                        <option value="0">Manual</option>
                    </select>
                    <div class="refresh-indicator" id="refresh-indicator">
                        <span id="refresh-countdown">--</span>
                    </div>
                    <button class="btn btn-outline" style="padding: 8px 12px;" onclick="refreshAllFeeds()">Refresh Now</button>
                    <button class="btn btn-outline" id="btn-detection-feeds" style="padding: 8px 12px;" onclick="toggleDetection()">CV: OFF</button>
                </div>
            </div>
            <div class="card-body">
                <div class="feeds-grid" id="feeds-grid">
                    <!-- Populated by JS -->
                </div>
            </div>
        </div>
    </div>

    <div id="tab-map" class="tab-content">
        <div class="map-container" style="height: calc(100vh - 280px); min-height: 600px; position: relative; border-radius: 10px; overflow: hidden; background: #0d1117;">
            <div id="tai-map" style="height: 100%; width: 100%;"></div>

            <!-- Map Controls -->
            <div class="map-overlay" style="position: absolute; top: 12px; left: 12px; z-index: 1000; display: flex; flex-direction: column; gap: 8px;">
                <button class="map-ctrl-btn" onclick="toggleFullscreen()" title="Fullscreen">⛶</button>
                <button class="map-ctrl-btn active" id="btn-show-vessels" onclick="toggleVessels()" title="Vessels">🚢</button>
                <button class="map-ctrl-btn active" id="btn-show-cameras" onclick="toggleCameras()" title="Cameras">📷</button>
                <button class="map-ctrl-btn" id="btn-show-routes" onclick="toggleRoutes()" title="Routes">🛤️</button>
                <div style="height: 1px; background: var(--border); margin: 4px 0;"></div>
                <button class="map-ctrl-btn" id="btn-detection" onclick="toggleDetection()" title="Toggle Detection">🎯</button>
            </div>

            <!-- TAI Drawing Panel -->
            <div class="map-overlay" style="position: absolute; bottom: 12px; left: 12px; z-index: 1000; display: flex; gap: 8px; align-items: center; background: rgba(22,27,34,0.95); padding: 10px 14px; border-radius: 8px; border: 1px solid #30363d; backdrop-filter: blur(8px);">
                <input type="text" id="new-tai-code" style="padding: 8px 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 12px; width: 100px; text-transform: uppercase;" placeholder="TAI Code">
                <button class="map-btn" id="btn-draw-tai" onclick="startDrawTai()">Draw TAI</button>
                <button class="map-btn secondary" onclick="clearAllTais()">Clear</button>
            </div>

            <!-- Vessel Panel -->
            <div class="vessel-panel map-overlay" style="position: absolute; top: 12px; right: 12px; width: 300px; max-height: calc(100% - 24px); background: rgba(22,27,34,0.95); border: 1px solid #30363d; border-radius: 10px; display: flex; flex-direction: column; backdrop-filter: blur(8px); z-index: 1000;">
                <div style="padding: 14px 16px; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 13px; font-weight: 700; color: #e6edf3; text-transform: uppercase; letter-spacing: 0.5px;">Active Vessels</span>
                    <span style="font-size: 10px; color: #6e7681;" id="vessels-update-time">--</span>
                </div>
                <div style="display: flex; gap: 8px; padding: 12px 16px; background: #161b22; border-bottom: 1px solid #30363d;">
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 22px; font-weight: 700; color: #58a6ff;" id="vessels-total">0</div>
                        <div style="font-size: 9px; color: #6e7681; text-transform: uppercase;">Total</div>
                    </div>
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 22px; font-weight: 700; color: #d29922;" id="vessels-underway">0</div>
                        <div style="font-size: 9px; color: #6e7681; text-transform: uppercase;">Underway</div>
                    </div>
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 22px; font-weight: 700; color: #3fb950;" id="vessels-docked">0</div>
                        <div style="font-size: 9px; color: #6e7681; text-transform: uppercase;">Docked</div>
                    </div>
                </div>
                <div style="padding: 10px 12px; border-bottom: 1px solid #30363d;">
                    <input type="text" id="vessel-search" style="width: 100%; padding: 8px 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 12px;" placeholder="Search vessels..." oninput="filterVesselList()">
                </div>
                <div id="vessel-list" style="flex: 1; overflow-y: auto; padding: 8px; max-height: 400px;">
                    <div style="padding: 20px; text-align: center; color: #6e7681;">Loading...</div>
                </div>
            </div>
        </div>
    </div>

    <div id="tab-reporting" class="tab-content">
        <div class="card">
            <div class="card-header">Operator Settings</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Callsign</span>
                    <input type="text" class="form-input" id="callsign" placeholder="PR01" maxlength="10">
                </div>
                <div class="divider"></div>
                <div class="section-title">Report Rate Limiting</div>
                <div class="form-row">
                    <span class="form-label">Min interval per TAI</span>
                    <input type="number" class="form-input" id="report-interval" min="5" max="300" step="5"> sec
                </div>
                <div class="form-row">
                    <span class="form-label">Confidence threshold</span>
                    <input type="number" class="form-input" id="conf-threshold" min="0.1" max="1.0" step="0.05">
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">Detection Settings</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Detection enabled</span>
                    <select class="form-input" id="detection-enabled">
                        <option value="true">Enabled</option>
                        <option value="false">Disabled</option>
                    </select>
                </div>
                <div class="form-row">
                    <span class="form-label">Model path</span>
                    <input type="text" class="form-input wide" id="model-path" placeholder="models/ferry_detector.pt">
                </div>
            </div>
        </div>
    </div>

    <div id="tab-cameras" class="tab-content">
        <div class="card">
            <div class="card-header">WSDOT Terminal Cameras</div>
            <div class="card-body">
                <div class="section-title">Select cameras to monitor</div>
                <div class="camera-grid" id="wsdot-cameras">
                    <!-- Populated by JS -->
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">Third-Party Cameras</div>
            <div class="card-body">
                <div class="camera-grid" id="thirdparty-cameras">
                    <!-- Populated by JS -->
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">Camera Settings</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Poll interval</span>
                    <input type="number" class="form-input" id="camera-poll-interval" min="10" max="120" step="5"> sec
                </div>
                <div class="form-row">
                    <span class="form-label">Save all frames</span>
                    <select class="form-input" id="save-all-frames">
                        <option value="true">Yes</option>
                        <option value="false">No (detections only)</option>
                    </select>
                </div>
                <div class="form-row">
                    <span class="form-label">Storage path</span>
                    <input type="text" class="form-input wide" id="storage-path" placeholder="./captures">
                </div>
            </div>
        </div>
    </div>

    <div id="tab-tai" class="tab-content">
        <div class="card">
            <div class="card-header">Target Area of Interest Mappings</div>
            <div class="card-body">
                <div class="section-title">Map TAI codes to terminals</div>
                <div id="tai-mappings">
                    <!-- Populated by JS -->
                </div>
                <div class="btn-row">
                    <button class="btn btn-outline" onclick="addTaiMapping()">+ Add TAI</button>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">Platform Codes</div>
            <div class="card-body">
                <div class="section-title">Vessel class to platform code mapping</div>
                <div class="form-row">
                    <span class="form-label">Olympic class</span>
                    <input type="text" class="form-input" id="platform-olympic" value="ORCA">
                </div>
                <div class="form-row">
                    <span class="form-label">Jumbo Mark II</span>
                    <input type="text" class="form-input" id="platform-jumbo" value="WHALE">
                </div>
                <div class="form-row">
                    <span class="form-label">Issaquah class</span>
                    <input type="text" class="form-input" id="platform-issaquah" value="SALMON">
                </div>
                <div class="form-row">
                    <span class="form-label">Super class</span>
                    <input type="text" class="form-input" id="platform-super" value="EAGLE">
                </div>
                <div class="form-row">
                    <span class="form-label">Kwa-di Tabil class</span>
                    <input type="text" class="form-input" id="platform-kwadi" value="SEAL">
                </div>
            </div>
        </div>
    </div>

    <div id="tab-chatsurfer" class="tab-content">
        <div class="card">
            <div class="card-header">WSDOT API Configuration</div>
            <div class="card-body">
                <div style="background: rgba(77, 159, 255, 0.1); border: 1px solid var(--accent); border-radius: 6px; padding: 12px; margin-bottom: 16px; font-size: 12px;">
                    <strong>Vessel Tracking:</strong> Real-time ferry positions require a WSDOT Traveler API key.
                    <a href="https://wsdot.wa.gov/traffic/api/" target="_blank" style="color: var(--accent);">Register here</a> (free).
                </div>
                <div class="form-row">
                    <span class="form-label">WSDOT API Key</span>
                    <input type="password" class="form-input wide" id="wsdot-api-key" placeholder="Enter your WSDOT API access code">
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">ChatSurfer Output</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Stream enabled</span>
                    <select class="form-input" id="cs-enabled">
                        <option value="false">Disabled</option>
                        <option value="true">Enabled</option>
                    </select>
                </div>
                <div class="form-row">
                    <span class="form-label">Output mode</span>
                    <select class="form-input" id="cs-mode" onchange="toggleCsFields()">
                        <option value="stdout">Console (stdout)</option>
                        <option value="file">File</option>
                        <option value="chatsurfer">ChatSurfer API</option>
                        <option value="webhook">Webhook</option>
                        <option value="websocket">WebSocket</option>
                    </select>
                </div>
            </div>
        </div>

        <div class="card" id="cs-api-card">
            <div class="card-header">ChatSurfer API Settings</div>
            <div class="card-body">
                <div style="background: rgba(77, 159, 255, 0.1); border: 1px solid var(--accent); border-radius: 6px; padding: 12px; margin-bottom: 16px; font-size: 12px;">
                    <strong>Authentication:</strong> Log in to ChatSurfer in your browser, then open DevTools (F12) → Application → Cookies → copy the SESSION cookie value.
                </div>
                <div class="form-row">
                    <span class="form-label">Session Cookie</span>
                    <input type="password" class="form-input wide" id="cs-session" placeholder="SESSION cookie value from browser">
                </div>
                <div class="form-row">
                    <span class="form-label">Room Name</span>
                    <input type="text" class="form-input" id="cs-room" placeholder="e.g., bf25_boat">
                </div>
                <div class="form-row">
                    <span class="form-label">Bot Nickname</span>
                    <input type="text" class="form-input" id="cs-nickname" value="OSINT_Bot" placeholder="OSINT_Bot">
                </div>
                <div class="form-row">
                    <span class="form-label">Domain ID</span>
                    <input type="text" class="form-input" id="cs-domain" value="chatsurferxmppunclass" placeholder="chatsurferxmppunclass">
                </div>
                <div class="form-row">
                    <span class="form-label">Server URL</span>
                    <input type="text" class="form-input wide" id="cs-server-url" value="https://chatsurfer.nro.mil" placeholder="https://chatsurfer.nro.mil">
                </div>
                <div class="form-row">
                    <span class="form-label">Classification</span>
                    <select class="form-input" id="cs-classification">
                        <option value="UNCLASSIFIED">UNCLASSIFIED</option>
                        <option value="UNCLASSIFIED//FOUO" selected>UNCLASSIFIED//FOUO</option>
                    </select>
                </div>
                <div class="btn-row">
                    <button class="btn btn-outline" onclick="testCsConnection()">Test Connection</button>
                </div>
            </div>
        </div>

        <div class="card" id="cs-webhook-card">
            <div class="card-header">Webhook Settings</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Webhook URL</span>
                    <input type="text" class="form-input wide" id="cs-webhook-url" placeholder="https://hooks.slack.com/...">
                </div>
            </div>
        </div>

        <div class="card" id="cs-websocket-card">
            <div class="card-header">WebSocket Settings</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">WebSocket URL</span>
                    <input type="text" class="form-input wide" id="cs-websocket-url" placeholder="wss://chatsurfer.example.com/ws">
                </div>
            </div>
        </div>

        <div class="card" id="cs-file-card">
            <div class="card-header">File Output</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Output file</span>
                    <input type="text" class="form-input wide" id="cs-output-file" placeholder="reports/tacreps.log">
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">Image Hosting</div>
            <div class="card-body">
                <div class="form-row">
                    <span class="form-label">Image base URL</span>
                    <input type="text" class="form-input wide" id="cs-image-url" placeholder="http://localhost:8080/images/">
                </div>
                <div style="font-size: 11px; color: var(--text-dim); margin-top: 8px;">
                    Detection images will be served from this URL. Include the trailing slash.
                </div>
            </div>
        </div>
    </div>

    <div id="tab-output" class="tab-content">
        <div style="display: grid; grid-template-columns: 1fr 320px; gap: 16px;">
            <div class="card">
                <div class="card-header" style="display: flex; justify-content: space-between; align-items: center;">
                    <span>Live TACREP Output</span>
                    <div style="display: flex; gap: 10px; align-items: center;">
                        <button class="btn btn-outline" style="padding: 6px 12px; font-size: 11px;" onclick="clearOutput()">Clear</button>
                        <label style="font-size: 11px; display: flex; align-items: center; gap: 6px;">
                            <input type="checkbox" id="auto-scroll" checked> Auto-scroll
                        </label>
                    </div>
                </div>
                <div class="card-body" style="padding: 0;">
                    <div class="tacrep-output" id="tacrep-output" style="max-height: 500px;">
                        <div style="color: var(--text-dim); padding: 20px; text-align: center;">
                            Waiting for reports...
                        </div>
                    </div>
                </div>
            </div>
            <div>
                <div class="card" style="margin-bottom: 16px;">
                    <div class="card-header">Quick Report</div>
                    <div class="card-body">
                        <div class="form-row">
                            <span class="form-label">TAI</span>
                            <input type="text" class="form-input" id="qr-tai" placeholder="e.g., BALDER" style="text-transform: uppercase;">
                        </div>
                        <div class="form-row">
                            <span class="form-label">Platform</span>
                            <select class="form-input" id="qr-platform">
                                <option value="WHALE">WHALE (Jumbo Mk II)</option>
                                <option value="EAGLE">EAGLE (Super)</option>
                                <option value="SALMON">SALMON (Issaquah)</option>
                                <option value="ORCA" selected>ORCA (Olympic)</option>
                                <option value="SEAL">SEAL (Kwa-di Tabil)</option>
                                <option value="UNKNOWN">UNKNOWN</option>
                            </select>
                        </div>
                        <div class="form-row">
                            <span class="form-label">Confidence</span>
                            <select class="form-input" id="qr-confidence">
                                <option value="CONFIRMED">CONFIRMED</option>
                                <option value="PROBABLE" selected>PROBABLE</option>
                                <option value="POSSIBLE">POSSIBLE</option>
                            </select>
                        </div>
                        <div class="form-row">
                            <span class="form-label">Targets</span>
                            <input type="number" class="form-input" id="qr-targets" value="1" min="1" max="10" style="width: 60px;">
                        </div>
                        <div class="form-row">
                            <span class="form-label">Remarks</span>
                            <input type="text" class="form-input wide" id="qr-remarks" placeholder="e.g., INBOUND OFFLOADING 45 VICS">
                        </div>
                        <button class="btn btn-primary" style="width: 100%; margin-top: 8px;" onclick="submitQuickReport()">Send TACREP</button>
                    </div>
                </div>
                <div class="card">
                    <div class="card-header">Deconfliction</div>
                    <div class="card-body" id="deconfliction-status" style="font-size: 12px; color: var(--text-dim);">
                        Loading...
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="btn-row">
        <button class="btn btn-primary" onclick="saveConfig()">Save Configuration</button>
        <button class="btn btn-outline" onclick="loadConfig()">Reload</button>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        let config = {};

        function switchTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab[onclick*="${name}"]`).classList.add('active');
            document.getElementById('tab-' + name).classList.add('active');
        }

        function toast(msg, isError = false) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.className = 'toast show ' + (isError ? 'error' : 'success');
            setTimeout(() => t.className = 'toast', 3000);
        }

        function setVal(id, val) {
            const el = document.getElementById(id);
            if (el) el.value = val ?? '';
        }

        function getVal(id, isNum = false) {
            const el = document.getElementById(id);
            if (!el) return null;
            return isNum ? parseFloat(el.value) : el.value;
        }

        async function loadStatus() {
            try {
                const r = await fetch('/api/status');
                const d = await r.json();

                document.getElementById('st-callsign').textContent = d.callsign || '--';
                document.getElementById('st-cameras').textContent = `${d.cameras_online || 0}/${d.cameras_total || 0}`;
                document.getElementById('st-reports').textContent = d.report_count || 0;
                document.getElementById('st-last-report').textContent = d.last_report_time || '--';

                const badge = document.getElementById('conn-badge');
                badge.textContent = d.running ? 'Running' : 'Stopped';
                badge.className = 'status-badge ' + (d.running ? 'ok' : 'error');
            } catch (e) {
                console.error(e);
            }
        }

        async function loadConfig() {
            try {
                const r = await fetch('/api/config');
                config = await r.json();

                // Reporting
                setVal('callsign', config.chatsurfer?.callsign);
                setVal('report-interval', config.chatsurfer?.min_report_interval_sec);
                setVal('conf-threshold', config.detector?.confidence_threshold);
                document.getElementById('detection-enabled').value = config.detector?.enabled ? 'true' : 'false';
                setVal('model-path', config.detector?.model_path);

                // Cameras
                setVal('camera-poll-interval', config.camera?.poll_interval_sec || 30);
                document.getElementById('save-all-frames').value = config.save_all_frames ? 'true' : 'false';
                setVal('storage-path', config.storage_path);

                // WSDOT API
                setVal('wsdot-api-key', config.wsdot_api_key);

                // ChatSurfer
                document.getElementById('cs-enabled').value = config.chatsurfer?.enabled ? 'true' : 'false';
                document.getElementById('cs-mode').value = config.chatsurfer?.mode || 'stdout';
                setVal('cs-session', config.chatsurfer?.session);
                setVal('cs-room', config.chatsurfer?.room);
                setVal('cs-nickname', config.chatsurfer?.nickname || 'OSINT_Bot');
                setVal('cs-domain', config.chatsurfer?.domain || 'chatsurferxmppunclass');
                setVal('cs-server-url', config.chatsurfer?.server_url || 'https://chatsurfer.nro.mil');
                document.getElementById('cs-classification').value = config.chatsurfer?.classification || 'UNCLASSIFIED//FOUO';
                setVal('cs-webhook-url', config.chatsurfer?.webhook_url);
                setVal('cs-websocket-url', config.chatsurfer?.websocket_url);
                setVal('cs-output-file', config.chatsurfer?.output_file);
                setVal('cs-image-url', config.chatsurfer?.image_base_url);
                toggleCsFields();

                // Load camera list
                await loadCameras();

                // Load TAI mappings
                loadTaiMappings();

                toast('Configuration loaded');
            } catch (e) {
                console.error(e);
                toast('Failed to load config', true);
            }
        }

        async function loadCameras() {
            try {
                const r = await fetch('/api/cameras');
                const cameras = await r.json();

                const wsdotGrid = document.getElementById('wsdot-cameras');
                const thirdpartyGrid = document.getElementById('thirdparty-cameras');

                wsdotGrid.innerHTML = '';
                thirdpartyGrid.innerHTML = '';

                cameras.forEach(cam => {
                    const item = document.createElement('label');
                    item.className = 'camera-item ' + (cam.online ? 'online' : 'offline');
                    item.innerHTML = `
                        <input type="checkbox" value="${cam.id}" ${cam.enabled ? 'checked' : ''}>
                        <span>${cam.name}</span>
                        ${cam.tai_code ? `<span class="camera-tai">${cam.tai_code}</span>` : ''}
                    `;

                    if (cam.source === 'wsdot') {
                        wsdotGrid.appendChild(item);
                    } else {
                        thirdpartyGrid.appendChild(item);
                    }
                });
            } catch (e) {
                console.error(e);
            }
        }

        function loadTaiMappings() {
            const container = document.getElementById('tai-mappings');
            const mappings = config.tai_codes || {
                'BALDER': { terminal: 'Point Defiance' },
                'THOR': { terminal: 'Tahlequah' }
            };

            container.innerHTML = '';

            Object.entries(mappings).forEach(([code, info]) => {
                const row = document.createElement('div');
                row.className = 'form-row';
                row.innerHTML = `
                    <input type="text" class="form-input" value="${code}" placeholder="TAI Code" style="width: 80px;">
                    <span style="margin: 0 10px; color: var(--text-dim);">→</span>
                    <input type="text" class="form-input" value="${info.terminal}" placeholder="Terminal name">
                    <button class="btn btn-outline" style="margin-left: 10px; padding: 8px 12px;" onclick="this.parentElement.remove()">×</button>
                `;
                container.appendChild(row);
            });
        }

        function addTaiMapping() {
            const container = document.getElementById('tai-mappings');
            const row = document.createElement('div');
            row.className = 'form-row';
            row.innerHTML = `
                <input type="text" class="form-input" placeholder="TAI Code" style="width: 80px;">
                <span style="margin: 0 10px; color: var(--text-dim);">→</span>
                <input type="text" class="form-input" placeholder="Terminal name">
                <button class="btn btn-outline" style="margin-left: 10px; padding: 8px 12px;" onclick="this.parentElement.remove()">×</button>
            `;
            container.appendChild(row);
        }

        async function saveConfig() {
            // Gather TAI mappings
            const taiMappings = {};
            document.querySelectorAll('#tai-mappings .form-row').forEach(row => {
                const inputs = row.querySelectorAll('input');
                if (inputs[0].value && inputs[1].value) {
                    taiMappings[inputs[0].value] = { terminal: inputs[1].value };
                }
            });

            // Gather enabled cameras
            const enabledCameras = [];
            document.querySelectorAll('.camera-item input:checked').forEach(cb => {
                enabledCameras.push(cb.value);
            });

            const cfg = {
                wsdot_api_key: getVal('wsdot-api-key'),
                chatsurfer: {
                    enabled: document.getElementById('cs-enabled').value === 'true',
                    callsign: getVal('callsign'),
                    min_report_interval_sec: getVal('report-interval', true),
                    mode: getVal('cs-mode'),
                    session: getVal('cs-session'),
                    room: getVal('cs-room'),
                    nickname: getVal('cs-nickname'),
                    domain: getVal('cs-domain'),
                    server_url: getVal('cs-server-url'),
                    classification: document.getElementById('cs-classification').value,
                    webhook_url: getVal('cs-webhook-url'),
                    websocket_url: getVal('cs-websocket-url'),
                    output_file: getVal('cs-output-file'),
                    image_base_url: getVal('cs-image-url'),
                },
                detector: {
                    enabled: document.getElementById('detection-enabled').value === 'true',
                    confidence_threshold: getVal('conf-threshold', true),
                    model_path: getVal('model-path'),
                },
                camera: {
                    poll_interval_sec: getVal('camera-poll-interval', true),
                },
                storage_path: getVal('storage-path'),
                save_all_frames: document.getElementById('save-all-frames').value === 'true',
                tai_codes: taiMappings,
                enabled_cameras: enabledCameras,
                platform_codes: {
                    Olympic: getVal('platform-olympic'),
                    'Jumbo Mark II': getVal('platform-jumbo'),
                    Issaquah: getVal('platform-issaquah'),
                    Super: getVal('platform-super'),
                    'Kwa-di Tabil': getVal('platform-kwadi'),
                },
            };

            try {
                const r = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(cfg)
                });
                const d = await r.json();
                toast(d.status === 'ok' ? 'Configuration saved' : (d.message || 'Failed'), d.status !== 'ok');
            } catch (e) {
                toast('Failed to save: ' + e, true);
            }
        }

        async function checkIn() {
            try {
                const r = await fetch('/api/checkin', { method: 'POST' });
                const d = await r.json();
                toast(d.status === 'ok' ? 'Checked in' : d.message, d.status !== 'ok');
                addTacrepOutput(d.message, 'status');
            } catch (e) {
                toast('Failed: ' + e, true);
            }
        }

        async function checkOut() {
            try {
                const r = await fetch('/api/checkout', { method: 'POST' });
                const d = await r.json();
                toast(d.status === 'ok' ? 'Checked out' : d.message, d.status !== 'ok');
                addTacrepOutput(d.message, 'status');
            } catch (e) {
                toast('Failed: ' + e, true);
            }
        }

        async function testReport() {
            try {
                const r = await fetch('/api/test-report', { method: 'POST' });
                const d = await r.json();
                if (d.tacrep) {
                    addTacrepOutput(d.tacrep, 'manual');
                }
                toast(d.status === 'ok' ? 'Test report sent' : d.message, d.status !== 'ok');
            } catch (e) {
                toast('Failed: ' + e, true);
            }
        }

        function addTacrepOutput(message, source = null) {
            const container = document.getElementById('tacrep-output');

            // Remove placeholder if exists
            if (container.querySelector('[style*="text-align: center"]')) {
                container.innerHTML = '';
            }

            const time = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = 'tacrep-entry';

            let sourceClass = 'manual';
            let sourceLabel = source || 'manual';
            if (source && source.includes('visual')) sourceClass = 'visual';
            else if (source && source.includes('api')) sourceClass = 'api';

            entry.innerHTML = `
                <span class="tacrep-time">${time}</span>
                <span class="tacrep-msg">${message}</span>
                <span class="tacrep-source ${sourceClass}">${sourceLabel}</span>
            `;
            container.appendChild(entry);

            // Auto-scroll
            if (document.getElementById('auto-scroll').checked) {
                container.scrollTop = container.scrollHeight;
            }
        }

        function clearOutput() {
            document.getElementById('tacrep-output').innerHTML = `
                <div style="color: var(--text-dim); padding: 20px; text-align: center;">
                    Waiting for reports...
                </div>
            `;
            _lastTacrepTimestamp = null;
        }

        // Poll for new TACREPs
        let _lastTacrepTimestamp = null;
        let _tacrepPollInterval = null;

        async function pollTacreps() {
            try {
                const url = _lastTacrepTimestamp
                    ? `/api/tacrep/recent?since=${encodeURIComponent(_lastTacrepTimestamp)}`
                    : '/api/tacrep/recent';
                const r = await fetch(url);
                const entries = await r.json();

                entries.forEach(e => {
                    addTacrepOutput(e.message, e.source);
                    _lastTacrepTimestamp = e.timestamp;
                });
            } catch (e) {
                // Silent - polling failure is not critical
            }
        }

        function startTacrepPolling() {
            if (_tacrepPollInterval) return;
            pollTacreps();
            _tacrepPollInterval = setInterval(pollTacreps, 3000);
        }

        // Start polling when output tab is shown
        document.querySelector('.tab[onclick*="output"]')?.addEventListener('click', () => {
            startTacrepPolling();
            loadDeconflictionStatus();
        });

        async function submitQuickReport() {
            const tai = document.getElementById('qr-tai').value.toUpperCase().trim();
            if (!tai) { toast('TAI code required', true); return; }

            try {
                const r = await fetch('/api/tacrep/manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tai: tai,
                        platform: document.getElementById('qr-platform').value,
                        confidence: document.getElementById('qr-confidence').value,
                        num_targets: parseInt(document.getElementById('qr-targets').value) || 1,
                        remarks: document.getElementById('qr-remarks').value.trim(),
                    })
                });
                const d = await r.json();
                if (d.status === 'ok') {
                    addTacrepOutput(d.tacrep, 'manual');
                    document.getElementById('qr-remarks').value = '';
                    toast('TACREP sent');
                } else {
                    toast(d.message || 'Failed', true);
                }
            } catch (e) {
                toast('Failed: ' + e, true);
            }
        }

        async function loadDeconflictionStatus() {
            try {
                const r = await fetch('/api/deconfliction/status');
                const d = await r.json();

                const el = document.getElementById('deconfliction-status');
                if (d.active_reports.length === 0) {
                    el.innerHTML = `
                        <div style="margin-bottom: 8px;">Window: <strong>${d.suppress_window_sec}s</strong> | Radius: <strong>${d.correlation_radius_nm} nm</strong></div>
                        <div>API vessels cached: <strong>${d.api_vessels_cached}</strong></div>
                        <div style="margin-top: 8px; color: var(--text-dim);">No active suppression windows</div>
                    `;
                } else {
                    const rows = d.active_reports.map(r => `
                        <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid var(--border);">
                            <span>${r.vessel_key}</span>
                            <span style="color: var(--text-dim);">${r.tai} | ${r.source} | ${r.age_sec}s ago${r.correlated ? ' | CORRELATED' : ''}</span>
                        </div>
                    `).join('');
                    el.innerHTML = `
                        <div style="margin-bottom: 8px;">Window: <strong>${d.suppress_window_sec}s</strong> | Radius: <strong>${d.correlation_radius_nm} nm</strong> | Cached: <strong>${d.api_vessels_cached}</strong></div>
                        <div style="font-size: 11px; margin-top: 8px;">${rows}</div>
                    `;
                }
            } catch (e) {
                // Silent
            }
        }

        // Refresh deconfliction status periodically when output tab is visible
        setInterval(() => {
            if (document.getElementById('tab-output')?.classList.contains('active')) {
                loadDeconflictionStatus();
            }
        }, 5000);

        // WebSocket for live updates (fallback - polling is primary)
        let ws = null;
        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws/tacrep`);

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'tacrep') {
                    addTacrepOutput(data.message, data.source);
                }
            };

            ws.onclose = () => {
                setTimeout(connectWebSocket, 3000);
            };
        }

        // ============== ENHANCED MAP ==============
        let map = null;
        let drawnItems = null;
        let drawControl = null;
        let vesselMarkers = {};
        let cameraMarkers = {};
        let routeLines = {};
        let taiAreas = {};
        let vesselData = {};
        let vesselRefreshInterval = null;
        let showVesselsEnabled = true;
        let showCamerasEnabled = true;
        let showRoutesEnabled = false;
        let currentDrawTai = null;
        let isFullscreen = false;

        // Vessel class colors
        const VESSEL_COLORS = {
            'JUMBO_MARK_II': '#58a6ff',
            'SUPER': '#a371f7',
            'ISSAQUAH': '#3fb950',
            'OLYMPIC': '#d29922',
            'KWA_DI_TABIL': '#f85149',
            'UNKNOWN': '#6e7681'
        };

        // WSF Routes - Actual navigation waypoints from OpenStreetMap ferry routes
        const WSF_ROUTES = [
            // Seattle-Bainbridge: OSM way 188300879 (21 pts)
            { name: 'Seattle-Bainbridge', color: '#58a6ff', coords: [
                [47.602895, -122.339841], [47.603235, -122.369035], [47.607064, -122.46353],
                [47.607361, -122.472051], [47.607593, -122.478728], [47.607795, -122.484545],
                [47.608008, -122.486504], [47.608543, -122.488455], [47.609458, -122.490161],
                [47.610737, -122.491431], [47.612121, -122.492328], [47.614877, -122.493835],
                [47.617651, -122.49531], [47.618508, -122.49599], [47.619207, -122.496502],
                [47.619937, -122.497376], [47.620365, -122.498152], [47.620737, -122.499161],
                [47.621135, -122.501097], [47.621408, -122.502634], [47.622114, -122.507041]
            ]},
            // Seattle-Bremerton: OSM way 5919117 (46 pts) + approach
            { name: 'Seattle-Bremerton', color: '#a371f7', coords: [
                [47.602023, -122.339864], [47.60227, -122.354525], [47.601664, -122.379257],
                [47.600783, -122.393169], [47.599274, -122.402416], [47.596358, -122.41257],
                [47.57656, -122.454041], [47.575216, -122.45675], [47.572753, -122.465349],
                [47.568131, -122.482522], [47.567027, -122.495066], [47.566967, -122.50585],
                [47.567288, -122.515281], [47.567917, -122.521995], [47.568875, -122.527044],
                [47.570088, -122.530613], [47.571707, -122.533125], [47.580726, -122.540059],
                [47.587506, -122.545272], [47.588231, -122.545943], [47.588814, -122.546768],
                [47.589688, -122.548003], [47.590833, -122.55046], [47.591264, -122.552752],
                [47.591367, -122.553299], [47.591161, -122.556702], [47.590344, -122.561218],
                [47.589162, -122.566544], [47.58881, -122.56765], [47.588132, -122.56978],
                [47.587583, -122.571321], [47.586423, -122.57382], [47.584901, -122.576632],
                [47.583065, -122.57981], [47.580961, -122.583008], [47.578149, -122.586765],
                [47.575342, -122.590014], [47.564852, -122.601499], [47.562895, -122.604091],
                [47.56101, -122.607456], [47.559627, -122.610739], [47.558913, -122.613612],
                [47.55878, -122.615962], [47.558987, -122.61889], [47.55949, -122.621423],
                [47.560208, -122.622783], [47.561199, -122.624443], [47.561747, -122.624913]
            ]},
            // Edmonds-Kingston: OSM way 198986193 (20 pts)
            { name: 'Edmonds-Kingston', color: '#3fb950', coords: [
                [47.813366, -122.385475], [47.813391, -122.385569], [47.813838, -122.387268],
                [47.814585, -122.389719], [47.815946, -122.39403], [47.816642, -122.397786],
                [47.816854, -122.401897], [47.816024, -122.407572], [47.814316, -122.41389],
                [47.810659, -122.423874], [47.803766, -122.440437], [47.797778, -122.454937],
                [47.794726, -122.463856], [47.792911, -122.47164], [47.792246, -122.48042],
                [47.792507, -122.486722], [47.793363, -122.490507], [47.794224, -122.49271],
                [47.794923, -122.494409], [47.794977, -122.494537]
            ]},
            // Mukilteo-Clinton: OSM way 5901929 (7 pts)
            { name: 'Mukilteo-Clinton', color: '#d29922', coords: [
                [47.95067, -122.297039], [47.951264, -122.297439], [47.956697, -122.312904],
                [47.962426, -122.321679], [47.9709, -122.341856], [47.972965, -122.3473],
                [47.974792, -122.349369]
            ]},
            // Fauntleroy-Vashon: OSM way 6420617 (9 pts)
            { name: 'Fauntleroy-Vashon', color: '#f85149', coords: [
                [47.52318, -122.396493], [47.522799, -122.400026], [47.52174, -122.409287],
                [47.515458, -122.453183], [47.514552, -122.458633], [47.514054, -122.460927],
                [47.513162, -122.462749], [47.51204, -122.463575], [47.510915, -122.463838]
            ]},
            // Vashon-Southworth: OSM way 5916039 (13 pts)
            { name: 'Vashon-Southworth', color: '#f85149', coords: [
                [47.512966, -122.495877], [47.514074, -122.494548], [47.515074, -122.492971],
                [47.515697, -122.490706], [47.515789, -122.487214], [47.515001, -122.476929],
                [47.514077, -122.470706], [47.513493, -122.466923], [47.512986, -122.465449],
                [47.512683, -122.465008], [47.51238, -122.464567], [47.511699, -122.464021],
                [47.510915, -122.463838]
            ]},
            // Fauntleroy-Southworth (Direct): OSM way 188542733 (8 pts)
            { name: 'Fauntleroy-Southworth', color: '#db61a2', coords: [
                [47.512966, -122.495877], [47.513965, -122.494935], [47.515222, -122.493425],
                [47.516115, -122.490808], [47.516951, -122.484524], [47.522805, -122.409105],
                [47.523356, -122.399835], [47.52318, -122.396493]
            ]},
            // Point Defiance-Tahlequah: OSM way 12189777 (10 pts)
            { name: 'Pt Defiance-Tahlequah', color: '#7ee787', coords: [
                [47.306322, -122.514139], [47.317006, -122.513971], [47.318087, -122.513809],
                [47.319163, -122.513584], [47.320233, -122.513298], [47.321294, -122.512951],
                [47.322344, -122.512543], [47.323384, -122.512075], [47.32441, -122.511547],
                [47.332006, -122.507785]
            ]},
            // Port Townsend-Coupeville: OSM way 232488600 (14 pts)
            { name: 'Pt Townsend-Coupeville', color: '#ff7b72', coords: [
                [48.111106, -122.759022], [48.110864, -122.758803], [48.109419, -122.757485],
                [48.108628, -122.756151], [48.108459, -122.754365], [48.108969, -122.752211],
                [48.110015, -122.749868], [48.116359, -122.737354], [48.147801, -122.672658],
                [48.149512, -122.670935], [48.1517, -122.670353], [48.15554, -122.671639],
                [48.159039, -122.672725], [48.159131, -122.672715]
            ]},
            // Anacortes-Lopez Island: OSM way 482056543 (38 pts)
            { name: 'Anacortes-Lopez', color: '#79c0ff', coords: [
                [48.508584, -122.676557], [48.509256, -122.67655], [48.510228, -122.676303],
                [48.511653, -122.676373], [48.513022, -122.676859], [48.514393, -122.677822],
                [48.515482, -122.679006], [48.516855, -122.680783], [48.518165, -122.683249],
                [48.519342, -122.685853], [48.520234, -122.688526], [48.521208, -122.692963],
                [48.52181, -122.697439], [48.522955, -122.708725], [48.5243, -122.722558],
                [48.525258, -122.734114], [48.526396, -122.746116], [48.528723, -122.786232],
                [48.528944, -122.791097], [48.528912, -122.797425], [48.529201, -122.807035],
                [48.529992, -122.812231], [48.532944, -122.822947], [48.534965, -122.826976],
                [48.537708, -122.830353], [48.541045, -122.833284], [48.564781, -122.85194],
                [48.567251, -122.854386], [48.568656, -122.856072], [48.570274, -122.858948],
                [48.571609, -122.862467], [48.572433, -122.86693], [48.572773, -122.872466],
                [48.572717, -122.876071], [48.572234, -122.878946], [48.571922, -122.880663],
                [48.570843, -122.883081]
            ]},
            // Lopez-Shaw Island: OSM way 507423344 (15 pts)
            { name: 'Lopez-Shaw', color: '#79c0ff', coords: [
                [48.570843, -122.883081], [48.572684, -122.8808], [48.574332, -122.879843],
                [48.577648, -122.879843], [48.580733, -122.882424], [48.582467, -122.886889],
                [48.584122, -122.895233], [48.586095, -122.906447], [48.588246, -122.921787],
                [48.588393, -122.92408], [48.588397, -122.925493], [48.588106, -122.927065],
                [48.587403, -122.92833], [48.586222, -122.929089], [48.584669, -122.929769]
            ]},
            // Shaw-Orcas Island: OSM way 507850512 (9 pts)
            { name: 'Shaw-Orcas', color: '#79c0ff', coords: [
                [48.584669, -122.929769], [48.585996, -122.930143], [48.586681, -122.930418],
                [48.587704, -122.931635], [48.592768, -122.939734], [48.593794, -122.941179],
                [48.594575, -122.94202], [48.596498, -122.94304], [48.597296, -122.943609]
            ]},
            // Orcas-Friday Harbor: OSM way 4780914 (46 pts)
            { name: 'Orcas-Friday Harbor', color: '#a5d6ff', coords: [
                [48.597296, -122.943609], [48.595716, -122.943105], [48.594961, -122.943148],
                [48.594637, -122.943405], [48.594325, -122.943989], [48.594109, -122.944881],
                [48.594052, -122.94598], [48.594166, -122.948057], [48.594956, -122.953809],
                [48.596943, -122.96083], [48.597448, -122.963152], [48.597395, -122.964909],
                [48.597194, -122.96686], [48.594193, -122.977868], [48.593175, -122.983756],
                [48.591948, -122.990351], [48.589819, -123.001207], [48.589658, -123.003053],
                [48.589554, -123.007144], [48.589306, -123.009115], [48.589062, -123.010901],
                [48.588584, -123.012445], [48.588419, -123.012979], [48.586295, -123.018005],
                [48.585564, -123.019393], [48.584543, -123.020893], [48.582726, -123.021919],
                [48.581483, -123.021957], [48.578871, -123.021271], [48.575577, -123.019726],
                [48.572397, -123.017494], [48.566312, -123.01157], [48.561929, -123.006235],
                [48.561452, -123.005742], [48.559684, -123.003911], [48.553103, -122.996591],
                [48.550528, -122.996107], [48.547491, -122.996955], [48.545227, -122.999013],
                [48.543558, -123.002617], [48.543064, -123.006371], [48.542449, -123.008904],
                [48.541523, -123.010348], [48.538309, -123.011902], [48.537087, -123.012761],
                [48.535678, -123.013985]
            ]},
            // Lopez-Friday Harbor: OSM way 482056544 (26 pts)
            { name: 'Lopez-Friday Harbor', color: '#a5d6ff', coords: [
                [48.570843, -122.883081], [48.5728, -122.881392], [48.573879, -122.88165],
                [48.574618, -122.88268], [48.575827, -122.886143], [48.575924, -122.889117],
                [48.574731, -122.893666], [48.572744, -122.897271], [48.569336, -122.901391],
                [48.566099, -122.904567], [48.559225, -122.912635], [48.546954, -122.931432],
                [48.543431, -122.938899], [48.541271, -122.94628], [48.540476, -122.956151],
                [48.541442, -122.968768], [48.543837, -122.98405], [48.544016, -122.987767],
                [48.543828, -122.99529], [48.542637, -123.006022], [48.54201, -123.008508],
                [48.541301, -123.009709], [48.540947, -123.009932], [48.538204, -123.011211],
                [48.536911, -123.012297], [48.535678, -123.013985]
            ]}
        ];

        // Terminal/Camera locations
        const CAMERA_LOCATIONS = {
            'clinton': { name: 'Clinton', lat: 47.9750, lon: -122.3519 },
            'mukilteo': { name: 'Mukilteo', lat: 47.9497, lon: -122.3046 },
            'edmonds': { name: 'Edmonds', lat: 47.8137, lon: -122.3838 },
            'kingston': { name: 'Kingston', lat: 47.7967, lon: -122.4942 },
            'bainbridge': { name: 'Bainbridge', lat: 47.6231, lon: -122.5103 },
            'seattle': { name: 'Seattle', lat: 47.6026, lon: -122.3393 },
            'bremerton': { name: 'Bremerton', lat: 47.5619, lon: -122.6247 },
            'fauntleroy': { name: 'Fauntleroy', lat: 47.5226, lon: -122.3928 },
            'vashon': { name: 'Vashon', lat: 47.5083, lon: -122.4635 },
            'southworth': { name: 'Southworth', lat: 47.5131, lon: -122.5006 },
            'pointdefiance': { name: 'Point Defiance', lat: 47.3059, lon: -122.5143 },
            'tahlequah': { name: 'Tahlequah', lat: 47.3349, lon: -122.5068 },
            'anacortes': { name: 'Anacortes', lat: 48.5074, lon: -122.6793 },
            'fridayharbor': { name: 'Friday Harbor', lat: 48.5357, lon: -123.0159 },
            'orcas': { name: 'Orcas', lat: 48.5975, lon: -122.9440 },
            'lopez': { name: 'Lopez', lat: 48.5706, lon: -122.8880 },
            'coupeville': { name: 'Coupeville (Keystone)', lat: 48.1591, lon: -122.6727 },
            'porttownsend': { name: 'Port Townsend', lat: 48.1126, lon: -122.7604 },
        };

        function initMap() {
            if (map) return;

            // Create map with dark tiles
            map = L.map('tai-map', {
                center: [47.6, -122.5],
                zoom: 9,
                zoomControl: false
            });

            // CartoDB Dark Matter tiles for sleek dark theme
            L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
                subdomains: 'abcd',
                maxZoom: 19
            }).addTo(map);

            // Add zoom control to bottom-right
            L.control.zoom({ position: 'bottomright' }).addTo(map);

            // Add route lines (shown by default)
            WSF_ROUTES.forEach(route => {
                const line = L.polyline(route.coords, {
                    color: route.color,
                    weight: 3,
                    opacity: 0.7,
                    dashArray: '10, 6'
                });
                line.bindTooltip(route.name, { permanent: false, direction: 'center', className: 'route-tooltip' });
                routeLines[route.name] = line;
                line.addTo(map);  // Show by default
            });
            showRoutesEnabled = true;
            document.getElementById('btn-show-routes')?.classList.add('active');

            // Initialize drawn items layer
            drawnItems = new L.FeatureGroup();
            map.addLayer(drawnItems);

            // Add draw control
            drawControl = new L.Control.Draw({
                draw: {
                    polygon: {
                        allowIntersection: false,
                        shapeOptions: { color: '#4d9fff', fillOpacity: 0.2 }
                    },
                    polyline: false,
                    rectangle: true,
                    circle: false,
                    circlemarker: false,
                    marker: false
                },
                edit: { featureGroup: drawnItems }
            });
            map.addControl(drawControl);

            // Handle draw events
            map.on(L.Draw.Event.CREATED, function(e) {
                const layer = e.layer;
                if (currentDrawTai) {
                    const color = getTaiColor(currentDrawTai);
                    layer.taiCode = currentDrawTai;
                    layer.setStyle({ color: color, fillColor: color, fillOpacity: 0.15, weight: 2 });
                    layer.bindPopup(`<b style="color: ${color};">TAI: ${currentDrawTai}</b>`);
                    taiAreas[currentDrawTai] = layer;
                    drawnItems.addLayer(layer);

                    assignCamerasToTai(currentDrawTai, layer);
                    saveTaiArea(currentDrawTai, layer, []);

                    currentDrawTai = null;
                    const btn = document.getElementById('btn-draw-tai');
                    btn.textContent = 'Draw TAI';
                    btn.classList.remove('drawing');
                    document.getElementById('new-tai-code').value = '';
                }
            });

            // Add camera markers with live feed popups
            Object.entries(CAMERA_LOCATIONS).forEach(([id, cam]) => {
                const marker = L.circleMarker([cam.lat, cam.lon], {
                    radius: 8,
                    fillColor: '#2dd4a0',
                    color: '#fff',
                    weight: 2,
                    fillOpacity: 0.8
                }).addTo(map);

                marker.on('click', () => {
                    const detResult = detectionResults[id];
                    const detBadge = detResult && detResult.detection_count > 0
                        ? `<span style="background: rgba(45,212,160,0.9); color: #000; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600;">${detResult.detection_count} vessel${detResult.detection_count > 1 ? 's' : ''} detected</span>`
                        : '';

                    marker.setPopupContent(`
                        <div style="min-width: 300px; max-width: 340px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #30363d;">
                                <div>
                                    <div style="font-size: 14px; font-weight: 700; color: #2dd4a0;">${cam.name}</div>
                                    <div style="font-size: 11px; color: #8b949e;">Terminal Camera</div>
                                </div>
                                ${detBadge}
                            </div>
                            <div style="border-radius: 8px; overflow: hidden; background: #000; margin-bottom: 8px;">
                                <img src="/api/feeds/${id}/snapshot?t=${Date.now()}"
                                     style="width: 100%; height: auto; display: block; min-height: 120px; object-fit: cover;"
                                     onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"
                                     alt="${cam.name} feed">
                                <div style="display: none; align-items: center; justify-content: center; height: 120px; color: #6e7681; font-size: 12px;">
                                    No feed available
                                </div>
                            </div>
                            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: #6e7681;">
                                <span>TAI: <span id="cam-tai-${id}" style="color: #e6edf3;">None</span></span>
                                <span>${cam.lat.toFixed(4)}, ${cam.lon.toFixed(4)}</span>
                            </div>
                        </div>
                    `);
                    marker.openPopup();
                });

                marker.bindPopup('');
                marker.camId = id;
                cameraMarkers[id] = marker;
            });

            // Load saved TAI areas
            loadTaiAreas();
        }

        function getTaiColor(taiCode) {
            // Generate consistent color from TAI code
            let hash = 0;
            for (let i = 0; i < taiCode.length; i++) {
                hash = taiCode.charCodeAt(i) + ((hash << 5) - hash);
            }
            const hue = hash % 360;
            return `hsl(${hue}, 70%, 50%)`;
        }

        function startDrawTai() {
            const taiCode = document.getElementById('new-tai-code').value.trim().toUpperCase();
            if (!taiCode) {
                toast('Enter a TAI code first', true);
                return;
            }

            if (taiAreas[taiCode]) {
                toast('TAI code already exists', true);
                return;
            }

            currentDrawTai = taiCode;
            const btn = document.getElementById('btn-draw-tai');
            btn.textContent = `Drawing: ${taiCode}`;
            btn.classList.add('drawing');
            toast(`Draw polygon for ${taiCode}. Double-click to finish.`);

            new L.Draw.Polygon(map, drawControl.options.draw.polygon).enable();
        }

        function assignCamerasToTai(taiCode, polygon) {
            const camerasInTai = [];

            Object.entries(CAMERA_LOCATIONS).forEach(([id, cam]) => {
                const point = L.latLng(cam.lat, cam.lon);
                if (isPointInPolygon(point, polygon)) {
                    camerasInTai.push(id);
                    // Update marker popup
                    const taiSpan = document.getElementById(`cam-tai-${id}`);
                    if (taiSpan) taiSpan.textContent = taiCode;

                    // Change marker color to match TAI
                    cameraMarkers[id].setStyle({ fillColor: getTaiColor(taiCode) });
                }
            });

            if (camerasInTai.length > 0) {
                toast(`Assigned ${camerasInTai.length} camera(s) to ${taiCode}`);
            }

            // Save to backend
            saveTaiArea(taiCode, polygon, camerasInTai);
        }

        function isPointInPolygon(point, polygon) {
            const bounds = polygon.getBounds();
            if (!bounds.contains(point)) return false;

            // More accurate check using ray casting
            const latlngs = polygon.getLatLngs()[0];
            let inside = false;
            for (let i = 0, j = latlngs.length - 1; i < latlngs.length; j = i++) {
                const xi = latlngs[i].lat, yi = latlngs[i].lng;
                const xj = latlngs[j].lat, yj = latlngs[j].lng;
                if (((yi > point.lng) !== (yj > point.lng)) &&
                    (point.lat < (xj - xi) * (point.lng - yi) / (yj - yi) + xi)) {
                    inside = !inside;
                }
            }
            return inside;
        }

        async function saveTaiArea(taiCode, polygon, cameras) {
            const coords = polygon.getLatLngs()[0].map(ll => [ll.lat, ll.lng]);
            try {
                await fetch('/api/tai-areas', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        code: taiCode,
                        polygon: coords,
                        cameras: cameras
                    })
                });
            } catch (e) {
                console.error('Failed to save TAI area:', e);
            }
        }

        async function loadTaiAreas() {
            try {
                const r = await fetch('/api/tai-areas');
                const areas = await r.json();

                areas.forEach(area => {
                    const coords = area.polygon.map(c => L.latLng(c[0], c[1]));
                    const polygon = L.polygon(coords, {
                        color: getTaiColor(area.code),
                        fillOpacity: 0.3
                    });
                    polygon.taiCode = area.code;
                    polygon.bindPopup(`<b>TAI: ${area.code}</b><br>Cameras: ${area.cameras.join(', ')}`);
                    taiAreas[area.code] = polygon;
                    drawnItems.addLayer(polygon);

                    // Update camera markers
                    area.cameras.forEach(camId => {
                        if (cameraMarkers[camId]) {
                            cameraMarkers[camId].setStyle({ fillColor: getTaiColor(area.code) });
                        }
                    });
                });

                updateTaiList();
            } catch (e) {
                console.error('Failed to load TAI areas:', e);
            }
        }

        function updateTaiList() {
            const container = document.getElementById('tai-list');
            if (Object.keys(taiAreas).length === 0) {
                container.innerHTML = '<div style="color: var(--text-dim);">No TAI areas defined. Draw one on the map above.</div>';
                return;
            }

            container.innerHTML = Object.entries(taiAreas).map(([code, polygon]) => {
                const cameras = [];
                Object.entries(CAMERA_LOCATIONS).forEach(([id, cam]) => {
                    if (isPointInPolygon(L.latLng(cam.lat, cam.lon), polygon)) {
                        cameras.push(cam.name);
                    }
                });

                return `
                    <div style="display: flex; align-items: center; padding: 10px; background: var(--bg); border-radius: 6px; margin-bottom: 8px;">
                        <div style="width: 12px; height: 12px; border-radius: 3px; background: ${getTaiColor(code)}; margin-right: 12px;"></div>
                        <div style="flex: 1;">
                            <div style="font-weight: 600;">${code}</div>
                            <div style="font-size: 11px; color: var(--text-dim);">Cameras: ${cameras.join(', ') || 'None'}</div>
                        </div>
                        <button class="btn btn-outline" style="padding: 6px 10px; font-size: 11px;" onclick="deleteTai('${code}')">Delete</button>
                    </div>
                `;
            }).join('');
        }

        async function deleteTai(code) {
            if (!confirm(`Delete TAI area "${code}"?`)) return;

            if (taiAreas[code]) {
                drawnItems.removeLayer(taiAreas[code]);
                delete taiAreas[code];
            }

            // Reset camera markers that were in this TAI
            Object.entries(cameraMarkers).forEach(([id, marker]) => {
                marker.setStyle({ fillColor: '#2dd4a0' });
            });

            // Re-apply remaining TAI colors
            Object.entries(taiAreas).forEach(([taiCode, polygon]) => {
                Object.entries(CAMERA_LOCATIONS).forEach(([id, cam]) => {
                    if (isPointInPolygon(L.latLng(cam.lat, cam.lon), polygon)) {
                        cameraMarkers[id].setStyle({ fillColor: getTaiColor(taiCode) });
                    }
                });
            });

            updateTaiList();

            try {
                await fetch(`/api/tai-areas/${code}`, { method: 'DELETE' });
            } catch (e) {
                console.error('Failed to delete TAI:', e);
            }
        }

        function clearAllTais() {
            if (!confirm('Delete all TAI areas?')) return;
            drawnItems.clearLayers();
            taiAreas = {};
            Object.values(cameraMarkers).forEach(m => m.setStyle({ fillColor: '#2dd4a0' }));
            updateTaiList();
        }

        // ============== VESSEL TRACKING ==============
        async function loadVessels() {
            try {
                const r = await fetch('/api/vessels');
                const vessels = await r.json();
                vesselData = vessels;
                updateVesselMarkers(vessels);
                updateVesselList(vessels);
                updateVesselStats(vessels);
                document.getElementById('vessels-update-time').textContent =
                    'Updated: ' + new Date().toLocaleTimeString();
            } catch (e) {
                console.error('Failed to load vessels:', e);
                document.getElementById('vessel-list').innerHTML =
                    `<div style="color: var(--danger);">Failed to load: ${e.message}</div>`;
            }
        }

        function updateVesselMarkers(vessels) {
            if (!map || !showVesselsEnabled) return;

            // Remove old markers not in new data
            Object.keys(vesselMarkers).forEach(id => {
                if (!vessels[id]) {
                    map.removeLayer(vesselMarkers[id]);
                    delete vesselMarkers[id];
                }
            });

            // Update or add markers
            Object.entries(vessels).forEach(([id, vessel]) => {
                if (!vessel.latitude || !vessel.longitude) return;
                if (vessel.latitude === 0 && vessel.longitude === 0) return;

                const pos = [vessel.latitude, vessel.longitude];
                const color = VESSEL_COLORS[vessel.vessel_class] || VESSEL_COLORS.UNKNOWN;
                const heading = vessel.heading || 0;
                const size = vessel.at_dock ? 28 : 32;
                const statusText = vessel.at_dock ? 'At Dock' : 'Underway';
                const statusColor = vessel.at_dock ? '#3fb950' : '#d29922';

                // SVG ship icon that rotates with heading
                const svgIcon = `
                    <svg width="${size}" height="${size}" viewBox="0 0 24 24" style="transform: rotate(${heading}deg); filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">
                        <path d="M12 2 L19 20 L12 15 L5 20 Z"
                              fill="${color}"
                              stroke="${vessel.at_dock ? '#1c2128' : '#fff'}"
                              stroke-width="1.5"
                              opacity="${vessel.at_dock ? 0.8 : 1}"/>
                    </svg>
                `;

                // Find nearest camera to this vessel
                const nearestCam = getNearestCamera(vessel.latitude, vessel.longitude);
                const distanceNM = nearestCam ? getDistanceNM(vessel.latitude, vessel.longitude, nearestCam.lat, nearestCam.lon).toFixed(1) : '?';

                // Enhanced popup with dark theme and camera feed
                const popupContent = `
                    <div style="min-width: 320px; max-width: 360px;">
                        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid #30363d;">
                            <span style="font-size: 24px;">🚢</span>
                            <div>
                                <div style="font-size: 15px; font-weight: 700; color: #58a6ff;">${vessel.name}</div>
                                <div style="font-size: 11px; color: #8b949e;">${formatVesselClass(vessel.vessel_class)} • ${vessel.platform_code}</div>
                            </div>
                            <span style="margin-left: auto; padding: 3px 8px; border-radius: 12px; font-size: 10px; font-weight: 600; background: ${statusColor}22; color: ${statusColor};">${statusText}</span>
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px;">
                            <div style="background: #161b22; border-radius: 6px; padding: 8px 10px;">
                                <div style="font-size: 10px; color: #6e7681; text-transform: uppercase;">Speed</div>
                                <div style="font-size: 14px; font-weight: 600; color: #e6edf3;">${(vessel.speed || 0).toFixed(1)} kts</div>
                            </div>
                            <div style="background: #161b22; border-radius: 6px; padding: 8px 10px;">
                                <div style="font-size: 10px; color: #6e7681; text-transform: uppercase;">Heading</div>
                                <div style="font-size: 14px; font-weight: 600; color: #e6edf3;">${(vessel.heading || 0).toFixed(0)}°</div>
                            </div>
                            ${vessel.departing_terminal || vessel.arriving_terminal ? `
                            <div style="grid-column: 1 / -1; background: #161b22; border-radius: 6px; padding: 8px 10px;">
                                <div style="font-size: 10px; color: #6e7681; text-transform: uppercase;">Route</div>
                                <div style="font-size: 13px; font-weight: 600; color: #e6edf3;">
                                    ${vessel.departing_terminal || '?'} <span style="color: #6e7681;">→</span> ${vessel.arriving_terminal || '?'}
                                </div>
                            </div>` : ''}
                            ${vessel.eta ? `
                            <div style="grid-column: 1 / -1; background: #161b22; border-radius: 6px; padding: 8px 10px;">
                                <div style="font-size: 10px; color: #6e7681; text-transform: uppercase;">ETA</div>
                                <div style="font-size: 13px; font-weight: 600; color: #e6edf3;">${new Date(vessel.eta).toLocaleTimeString()}</div>
                            </div>` : ''}
                        </div>
                        ${nearestCam ? `
                        <div style="border-top: 1px solid #30363d; padding-top: 12px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                                <div style="font-size: 11px; color: #6e7681; text-transform: uppercase; font-weight: 600;">Nearest Camera</div>
                                <div style="font-size: 11px; color: #8b949e;">📷 ${nearestCam.name} • ${distanceNM} nm</div>
                            </div>
                            <div style="position: relative; border-radius: 8px; overflow: hidden; background: #000;">
                                <img src="/api/feeds/${nearestCam.id}/snapshot?t=${Date.now()}"
                                     style="width: 100%; height: auto; display: block; min-height: 120px; object-fit: cover;"
                                     onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"
                                     alt="${nearestCam.name} feed">
                                <div style="display: none; align-items: center; justify-content: center; height: 120px; color: #6e7681; font-size: 12px;">
                                    Camera offline or no data
                                </div>
                            </div>
                        </div>` : ''}
                    </div>
                `;

                if (vesselMarkers[id]) {
                    vesselMarkers[id].setLatLng(pos);
                    vesselMarkers[id].setIcon(L.divIcon({
                        className: 'vessel-icon',
                        html: svgIcon,
                        iconSize: [size, size],
                        iconAnchor: [size/2, size/2]
                    }));
                    vesselMarkers[id].setPopupContent(popupContent);
                } else {
                    const marker = L.marker(pos, {
                        icon: L.divIcon({
                            className: 'vessel-icon',
                            html: svgIcon,
                            iconSize: [size, size],
                            iconAnchor: [size/2, size/2]
                        })
                    }).bindPopup(popupContent).addTo(map);

                    vesselMarkers[id] = marker;
                }
            });
        }

        function formatVesselClass(cls) {
            if (!cls) return 'Unknown';
            return cls.replace(/_/g, ' ').toLowerCase().replace(/\\b\\w/g, c => c.toUpperCase());
        }

        function getNearestCamera(lat, lon) {
            let nearest = null;
            let minDist = Infinity;

            Object.entries(CAMERA_LOCATIONS).forEach(([id, cam]) => {
                // Haversine-ish distance (simplified for small distances)
                const dLat = cam.lat - lat;
                const dLon = cam.lon - lon;
                const dist = Math.sqrt(dLat * dLat + dLon * dLon);

                if (dist < minDist) {
                    minDist = dist;
                    nearest = { id, ...cam, distance: dist };
                }
            });

            return nearest;
        }

        function getDistanceNM(lat1, lon1, lat2, lon2) {
            // Haversine formula for nautical miles
            const R = 3440.065; // Earth radius in nautical miles
            const dLat = (lat2 - lat1) * Math.PI / 180;
            const dLon = (lon2 - lon1) * Math.PI / 180;
            const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
                      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
                      Math.sin(dLon/2) * Math.sin(dLon/2);
            const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
            return R * c;
        }

        function updateVesselList(vessels) {
            const container = document.getElementById('vessel-list');
            const searchTerm = document.getElementById('vessel-search')?.value?.toLowerCase() || '';
            const vesselArray = Object.entries(vessels)
                .filter(([id, v]) => v.in_service && v.latitude)
                .filter(([id, v]) => !searchTerm || v.name.toLowerCase().includes(searchTerm))
                .sort((a, b) => a[1].name.localeCompare(b[1].name));

            if (vesselArray.length === 0) {
                container.innerHTML = '<div style="padding: 20px; text-align: center; color: #6e7681;">No vessels found</div>';
                return;
            }

            container.innerHTML = vesselArray.map(([id, v]) => {
                const color = VESSEL_COLORS[v.vessel_class] || VESSEL_COLORS.UNKNOWN;
                const route = v.at_dock
                    ? `Docked at ${v.departing_terminal || 'terminal'}`
                    : `${v.departing_terminal || '?'} → ${v.arriving_terminal || '?'}`;
                return `
                <div class="vessel-item" onclick="focusVessel('${id}')">
                    <span class="vessel-item-icon" style="color: ${color};">🚢</span>
                    <div class="vessel-item-info">
                        <div class="vessel-item-name">${v.name}</div>
                        <div class="vessel-item-route">${route}</div>
                    </div>
                    <span class="vessel-item-speed">${(v.speed || 0).toFixed(1)} kts</span>
                </div>`;
            }).join('');
        }

        function updateVesselStats(vessels) {
            const active = Object.values(vessels).filter(v => v.in_service && v.latitude);
            const underway = active.filter(v => !v.at_dock);
            const docked = active.filter(v => v.at_dock);

            document.getElementById('vessels-total').textContent = active.length;
            document.getElementById('vessels-underway').textContent = underway.length;
            document.getElementById('vessels-docked').textContent = docked.length;
        }

        function focusVessel(id) {
            if (vesselMarkers[id] && map) {
                map.setView(vesselMarkers[id].getLatLng(), 13, { animate: true });
                vesselMarkers[id].openPopup();
            }
        }

        function toggleVessels() {
            showVesselsEnabled = !showVesselsEnabled;
            document.getElementById('btn-show-vessels').classList.toggle('active', showVesselsEnabled);
            Object.values(vesselMarkers).forEach(m => {
                if (showVesselsEnabled) m.addTo(map);
                else map.removeLayer(m);
            });
        }

        function toggleCameras() {
            showCamerasEnabled = !showCamerasEnabled;
            document.getElementById('btn-show-cameras').classList.toggle('active', showCamerasEnabled);
            Object.values(cameraMarkers).forEach(m => {
                if (showCamerasEnabled) m.addTo(map);
                else map.removeLayer(m);
            });
        }

        function toggleRoutes() {
            showRoutesEnabled = !showRoutesEnabled;
            document.getElementById('btn-show-routes').classList.toggle('active', showRoutesEnabled);
            Object.values(routeLines).forEach(line => {
                if (showRoutesEnabled) line.addTo(map);
                else map.removeLayer(line);
            });
        }

        function toggleFullscreen() {
            const container = document.querySelector('.map-container');
            isFullscreen = !isFullscreen;
            container.classList.toggle('map-fullscreen', isFullscreen);
            setTimeout(() => map.invalidateSize(), 100);
        }

        // ============== DETECTION ==============
        let detectionEnabled = false;
        let detectionResults = {};
        let detectionInterval = null;

        function updateDetectionButtons(enabled) {
            const mapBtn = document.getElementById('btn-detection');
            const feedsBtn = document.getElementById('btn-detection-feeds');
            if (mapBtn) {
                mapBtn.classList.toggle('active', enabled);
                mapBtn.title = enabled ? 'Detection ON - Click to disable' : 'Detection OFF - Click to enable';
            }
            if (feedsBtn) {
                feedsBtn.textContent = enabled ? 'CV: ON' : 'CV: OFF';
                feedsBtn.style.background = enabled ? 'var(--accent)' : '';
                feedsBtn.style.borderColor = enabled ? 'var(--accent)' : '';
                feedsBtn.style.color = enabled ? '#fff' : '';
            }
            const stEl = document.getElementById('st-detection');
            if (stEl) {
                stEl.textContent = enabled ? 'ON' : 'OFF';
                stEl.className = 'value ' + (enabled ? 'ok' : '');
            }
        }

        async function toggleDetection() {
            const mapBtn = document.getElementById('btn-detection');
            const feedsBtn = document.getElementById('btn-detection-feeds');

            if (!detectionEnabled) {
                // Enable detection
                if (mapBtn) mapBtn.classList.add('loading');
                if (feedsBtn) feedsBtn.textContent = 'CV: ...';
                try {
                    const r = await fetch('/api/detection/enable', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enable: true, confidence_threshold: 0.25 })
                    });
                    const data = await r.json();
                    if (data.status === 'ok') {
                        detectionEnabled = true;
                        updateDetectionButtons(true);
                        showNotification('Detection enabled - bounding boxes active', 'success');
                        startDetectionScanning();
                        refreshAllFeeds();
                    } else {
                        showNotification(data.message || 'Failed to enable detection', 'error');
                        updateDetectionButtons(false);
                    }
                } catch (e) {
                    showNotification('Failed to enable detection: ' + e.message, 'error');
                    updateDetectionButtons(false);
                }
                if (mapBtn) mapBtn.classList.remove('loading');
            } else {
                // Disable detection
                try {
                    await fetch('/api/detection/enable', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enable: false })
                    });
                    detectionEnabled = false;
                    updateDetectionButtons(false);
                    showNotification('Detection disabled', 'warning');
                    stopDetectionScanning();
                    refreshAllFeeds();
                } catch (e) {
                    console.error('Failed to disable detection:', e);
                }
            }
        }

        function startDetectionScanning() {
            if (detectionInterval) return;
            runDetectionScan();
            detectionInterval = setInterval(runDetectionScan, 10000); // Every 10 seconds
        }

        function stopDetectionScanning() {
            if (detectionInterval) {
                clearInterval(detectionInterval);
                detectionInterval = null;
            }
            detectionResults = {};
            updateDetectionOverlays();
        }

        async function runDetectionScan() {
            if (!detectionEnabled) return;

            try {
                const r = await fetch('/api/detection/scan-all', { method: 'POST' });
                const data = await r.json();

                if (data.status === 'ok') {
                    // Update detection count in status bar
                    const statusEl = document.getElementById('st-detection');
                    if (statusEl) {
                        statusEl.textContent = data.total_detections > 0 ? `${data.total_detections} found` : 'ON';
                        statusEl.className = 'value ' + (data.total_detections > 0 ? 'ok' : '');
                    }

                    // Fetch full results
                    const resultsR = await fetch('/api/detection/results');
                    detectionResults = await resultsR.json();
                    updateDetectionOverlays();
                }
            } catch (e) {
                console.error('Detection scan error:', e);
            }
        }

        function updateDetectionOverlays() {
            // Update feed cards with detection info
            document.querySelectorAll('.feed-card').forEach(card => {
                const feedId = card.dataset.feedId;
                const result = detectionResults[feedId];

                // Remove existing overlay
                const existing = card.querySelector('.detection-overlay');
                if (existing) existing.remove();

                if (result && result.detection_count > 0) {
                    const overlay = document.createElement('div');
                    overlay.className = 'detection-overlay';
                    overlay.innerHTML = `
                        <span class="detection-badge">${result.detection_count} vessel${result.detection_count > 1 ? 's' : ''}</span>
                    `;
                    card.querySelector('.feed-image-container').appendChild(overlay);
                }
            });
        }

        function filterVesselList() {
            updateVesselList(vesselData);
        }

        // Start vessel tracking when map tab is shown
        function startVesselTracking() {
            if (vesselRefreshInterval) return;
            loadVessels();
            vesselRefreshInterval = setInterval(loadVessels, 5000);
        }

        // Initialize map when tab is clicked
        document.querySelector('.tab[onclick*="map"]').addEventListener('click', () => {
            setTimeout(() => {
                initMap();
                startVesselTracking();
            }, 100);
        });

        // ============== LIVE FEEDS ==============
        let feedsData = {};
        let feedRefreshInterval = null;
        let refreshRate = 5000;
        let refreshCountdown = 5;

        async function loadFeeds() {
            try {
                const r = await fetch('/api/feeds');
                feedsData = await r.json();
                renderFeedsGrid();
            } catch (e) {
                console.error('Failed to load feeds:', e);
            }
        }

        function renderFeedsGrid() {
            const grid = document.getElementById('feeds-grid');
            const filter = document.getElementById('feeds-filter').value;

            let feeds = Object.entries(feedsData);

            // Apply filter
            if (filter === 'online') {
                feeds = feeds.filter(([id, f]) => f.online);
            } else if (filter === 'enabled') {
                feeds = feeds.filter(([id, f]) => f.enabled);
            }

            if (feeds.length === 0) {
                grid.innerHTML = '<div style="color: var(--text-dim); padding: 40px; text-align: center;">No cameras match the filter.</div>';
                return;
            }

            grid.innerHTML = feeds.map(([id, feed]) => {
                const statusClass = !feed.enabled ? 'disabled' : (feed.online ? '' : 'offline');
                const statusDot = feed.online ? 'online' : '';
                const imgSrc = feed.enabled && feed.online
                    ? (detectionEnabled ? `/api/detection/detect/${id}/annotated?t=${Date.now()}` : `/api/feeds/${id}/snapshot?t=${Date.now()}`)
                    : '';

                return `
                    <div class="feed-card ${statusClass}" data-feed-id="${id}">
                        <div class="feed-image-container">
                            ${imgSrc ? `<img class="feed-image" src="${imgSrc}" alt="${feed.name}" onerror="this.style.display='none'">` : ''}
                            <div class="feed-placeholder">${feed.enabled ? (feed.online ? '' : 'OFFLINE') : 'DISABLED'}</div>
                        </div>
                        <div class="feed-info">
                            <div class="feed-header">
                                <span class="feed-name">${feed.name}</span>
                                <div class="feed-status ${statusDot}"></div>
                            </div>
                            <div class="feed-meta">
                                ${feed.tai_code ? `<span class="camera-tai">${feed.tai_code}</span> ` : ''}
                                ${feed.last_update ? `Updated: ${new Date(feed.last_update).toLocaleTimeString()}` : 'No data yet'}
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function refreshAllFeeds() {
            // Reload images - use annotated endpoint when detection is active
            document.querySelectorAll('.feed-image').forEach(img => {
                const feedId = img.closest('.feed-card').dataset.feedId;
                img.src = detectionEnabled
                    ? `/api/detection/detect/${feedId}/annotated?t=${Date.now()}`
                    : `/api/feeds/${feedId}/snapshot?t=${Date.now()}`;
            });
            refreshCountdown = refreshRate / 1000;
        }

        function filterFeeds() {
            renderFeedsGrid();
        }

        function setRefreshRate() {
            refreshRate = parseInt(document.getElementById('refresh-rate').value);

            if (feedRefreshInterval) {
                clearInterval(feedRefreshInterval);
                feedRefreshInterval = null;
            }

            if (refreshRate > 0) {
                refreshCountdown = refreshRate / 1000;
                feedRefreshInterval = setInterval(() => {
                    refreshCountdown--;
                    if (refreshCountdown <= 0) {
                        refreshAllFeeds();
                    }
                    updateRefreshIndicator();
                }, 1000);
            }
            updateRefreshIndicator();
        }

        function updateRefreshIndicator() {
            const indicator = document.getElementById('refresh-indicator');
            const countdown = document.getElementById('refresh-countdown');
            if (refreshRate > 0) {
                countdown.textContent = `${refreshCountdown}s`;
                indicator.classList.add('active');
            } else {
                countdown.textContent = 'Manual';
                indicator.classList.remove('active');
            }
        }

        // ============== CHATSURFER CONFIG ==============
        function toggleCsFields() {
            const mode = document.getElementById('cs-mode').value;
            document.getElementById('cs-api-card').style.display = mode === 'chatsurfer' ? 'block' : 'none';
            document.getElementById('cs-webhook-card').style.display = mode === 'webhook' ? 'block' : 'none';
            document.getElementById('cs-websocket-card').style.display = mode === 'websocket' ? 'block' : 'none';
            document.getElementById('cs-file-card').style.display = mode === 'file' ? 'block' : 'none';
        }

        async function testCsConnection() {
            const session = document.getElementById('cs-session').value;
            const room = document.getElementById('cs-room').value;

            if (!session || !room) {
                toast('Session cookie and room name are required', true);
                return;
            }

            try {
                const r = await fetch('/api/chatsurfer/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session: session,
                        room: room,
                        nickname: document.getElementById('cs-nickname').value,
                        domain: document.getElementById('cs-domain').value,
                        server_url: document.getElementById('cs-server-url').value,
                    })
                });
                const d = await r.json();
                toast(d.status === 'ok' ? 'Connection successful!' : (d.message || 'Connection failed'), d.status !== 'ok');
            } catch (e) {
                toast('Test failed: ' + e, true);
            }
        }

        // Initialize
        loadConfig();
        loadStatus();
        loadFeeds();
        setInterval(loadStatus, 5000);
        setRefreshRate();
        toggleCsFields();
        // connectWebSocket();  // Enable when WebSocket endpoint is ready
    </script>

    <!-- Feed Lightbox Modal -->
    <div class="feed-lightbox" id="feed-lightbox" onclick="closeLightbox(event)">
        <button class="feed-lightbox-close" onclick="closeLightbox()">&times;</button>
        <div class="feed-lightbox-header">
            <span class="feed-name" id="lightbox-name"></span>
            <span class="camera-tai" id="lightbox-tai" style="display:none;"></span>
            <span id="lightbox-time" style="color: #8b949e; font-size: 13px;"></span>
        </div>
        <img id="lightbox-img" src="" alt="">
        <div class="feed-lightbox-nav">
            <button onclick="lightboxPrev(event)">&larr; Prev</button>
            <button onclick="lightboxRefresh(event)">Refresh</button>
            <button onclick="lightboxNext(event)">Next &rarr;</button>
        </div>
    </div>
    <script>
        let lightboxFeedId = null;
        let lightboxFeedList = [];

        function openLightbox(feedId) {
            lightboxFeedId = feedId;
            lightboxFeedList = Object.entries(feedsData)
                .filter(([id, f]) => f.enabled && f.online)
                .map(([id]) => id);
            updateLightboxContent();
            document.getElementById('feed-lightbox').classList.add('open');
            document.addEventListener('keydown', lightboxKeyHandler);
        }

        function closeLightbox(e) {
            if (e && e.target !== document.getElementById('feed-lightbox') && e.target !== document.querySelector('.feed-lightbox-close')) return;
            document.getElementById('feed-lightbox').classList.remove('open');
            document.removeEventListener('keydown', lightboxKeyHandler);
        }

        function updateLightboxContent() {
            const feed = feedsData[lightboxFeedId];
            if (!feed) return;
            const imgSrc = detectionEnabled
                ? `/api/detection/detect/${lightboxFeedId}/annotated?t=${Date.now()}`
                : `/api/feeds/${lightboxFeedId}/snapshot?t=${Date.now()}`;
            document.getElementById('lightbox-img').src = imgSrc;
            document.getElementById('lightbox-name').textContent = feed.name;
            const taiEl = document.getElementById('lightbox-tai');
            if (feed.tai_code) { taiEl.textContent = feed.tai_code; taiEl.style.display = ''; }
            else { taiEl.style.display = 'none'; }
            document.getElementById('lightbox-time').textContent = feed.last_update
                ? new Date(feed.last_update).toLocaleTimeString() : '';
        }

        function lightboxNav(dir) {
            const idx = lightboxFeedList.indexOf(lightboxFeedId);
            if (idx === -1) return;
            const next = (idx + dir + lightboxFeedList.length) % lightboxFeedList.length;
            lightboxFeedId = lightboxFeedList[next];
            updateLightboxContent();
        }

        function lightboxPrev(e) { e && e.stopPropagation(); lightboxNav(-1); }
        function lightboxNext(e) { e && e.stopPropagation(); lightboxNav(1); }
        function lightboxRefresh(e) { e && e.stopPropagation(); updateLightboxContent(); }

        function lightboxKeyHandler(e) {
            if (e.key === 'Escape') closeLightbox();
            if (e.key === 'ArrowLeft') lightboxNav(-1);
            if (e.key === 'ArrowRight') lightboxNav(1);
            if (e.key === 'r' || e.key === ' ') { e.preventDefault(); updateLightboxContent(); }
        }

        // Attach click handlers to feed cards (delegated)
        document.getElementById('feeds-grid').addEventListener('click', (e) => {
            const card = e.target.closest('.feed-card');
            if (!card) return;
            const feedId = card.dataset.feedId;
            const feed = feedsData[feedId];
            if (feed && feed.enabled && feed.online) {
                openLightbox(feedId);
            }
        });
    </script>
</body>
</html>
"""


def create_app(osint_app: "PugetSoundOSINT") -> FastAPI:
    """Create FastAPI application with routes."""
    app = FastAPI(title="Puget Sound OSINT", version="0.1.0")
    app.state.osint = osint_app

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return CONFIG_PAGE

    @app.get("/api/status")
    async def get_status():
        osint = app.state.osint
        feeds_status = osint._feed_manager.get_status() if osint._feed_manager else {}
        online = sum(1 for f in feeds_status.values() if f.get("online", False))

        return {
            "running": osint._running,
            "callsign": osint._chatsurfer.tacrep_gen.callsign if osint._chatsurfer else "PR01",
            "cameras_online": online,
            "cameras_total": len(feeds_status),
            "report_count": osint._chatsurfer.tacrep_gen._serial_counter if osint._chatsurfer else 0,
            "last_report_time": None,  # TODO: Track this
        }

    @app.get("/api/config")
    async def get_config():
        return app.state.osint.config

    @app.post("/api/config")
    async def update_config(request: Request):
        try:
            new_config = await request.json()
            osint = app.state.osint

            # Merge config
            def merge(base, update):
                for k, v in update.items():
                    if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                        merge(base[k], v)
                    else:
                        base[k] = v

            merge(osint.config, new_config)

            # Update ChatSurfer client settings
            if osint._chatsurfer and "chatsurfer" in new_config:
                cs = new_config["chatsurfer"]
                if "callsign" in cs:
                    osint._chatsurfer.tacrep_gen.callsign = cs["callsign"]
                # Propagate session, room, server_url, nickname, domain to client
                for key in ("session", "room", "server_url", "nickname", "domain",
                            "classification", "callsign", "mode"):
                    if key in cs:
                        setattr(osint._chatsurfer.config, key, cs[key])

            # Reset vessel client if API key changed (so it picks up new key)
            if "wsdot_api_key" in new_config:
                app.state.vessel_client = None
                app.state.vessel_cache = None

            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Config update error: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

    @app.get("/api/cameras")
    async def get_cameras():
        osint = app.state.osint
        if not osint._feed_manager:
            return []

        cameras = []
        for feed_id, feed in osint._feed_manager.feeds.items():
            cameras.append({
                "id": feed_id,
                "name": feed.name,
                "online": feed.is_online,
                "enabled": feed.enabled,
                "tai_code": feed.tai_code,
                "source": "wsdot" if "wsdot" in feed.url else "thirdparty",
            })
        return cameras

    @app.get("/api/feeds")
    async def get_feeds():
        """Get all feeds with status for live viewer."""
        osint = app.state.osint
        if not osint._feed_manager:
            return {}

        return {
            feed_id: {
                "name": feed.name,
                "enabled": feed.enabled,
                "online": feed.is_online,
                "tai_code": feed.tai_code,
                "last_update": feed.last_frame_time.isoformat() if feed.last_frame_time else None,
                "errors": feed.consecutive_errors,
                "coordinates": {"lat": feed.coordinates[0], "lon": feed.coordinates[1]},
            }
            for feed_id, feed in osint._feed_manager.feeds.items()
        }

    @app.get("/api/feeds/{feed_id}/snapshot")
    async def get_feed_snapshot(feed_id: str):
        """Get latest snapshot image for a feed."""
        osint = app.state.osint
        if not osint._feed_manager:
            raise HTTPException(status_code=503, detail="Feed manager not initialized")

        feed = osint._feed_manager.get_feed(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail=f"Feed not found: {feed_id}")

        if feed.last_frame is None:
            raise HTTPException(status_code=404, detail="No frame available")

        # Encode frame to JPEG
        _, buffer = cv2.imencode('.jpg', feed.last_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

        return Response(
            content=buffer.tobytes(),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    @app.post("/api/checkin")
    async def check_in():
        osint = app.state.osint
        if osint._chatsurfer:
            msg = osint._chatsurfer.tacrep_gen.generate_checkin()
            osint._chatsurfer.check_in()
            return {"status": "ok", "message": msg}
        return JSONResponse({"status": "error", "message": "ChatSurfer not initialized"}, status_code=400)

    @app.post("/api/checkout")
    async def check_out():
        osint = app.state.osint
        if osint._chatsurfer:
            msg = osint._chatsurfer.tacrep_gen.generate_checkout()
            osint._chatsurfer.check_out()
            return {"status": "ok", "message": msg}
        return JSONResponse({"status": "error", "message": "ChatSurfer not initialized"}, status_code=400)

    @app.post("/api/test-report")
    async def test_report():
        osint = app.state.osint
        if not osint._chatsurfer:
            return JSONResponse({"status": "error", "message": "ChatSurfer not initialized"}, status_code=400)

        from ..reporting.tacrep import ConfidenceLevel

        report = osint._chatsurfer.tacrep_gen.create_report(
            num_targets=1,
            confidence=ConfidenceLevel.PROBABLE,
            platform="ORCA",
            tai="TEST",
            remarks="TEST REPORT",
        )
        osint._chatsurfer.send_report(report)

        return {
            "status": "ok",
            "tacrep": report.to_tacrep_string(),
            "image_url": None,
        }

    # TAI Areas storage (in-memory, should be persisted to file/db)
    app.state.tai_areas = []

    @app.get("/api/tai-areas")
    async def get_tai_areas():
        return app.state.tai_areas

    @app.post("/api/tai-areas")
    async def save_tai_area(request: Request):
        try:
            data = await request.json()
            code = data.get("code")
            polygon = data.get("polygon")
            cameras = data.get("cameras", [])

            if not code or not polygon:
                return JSONResponse({"status": "error", "message": "Missing code or polygon"}, status_code=400)

            # Remove existing area with same code
            app.state.tai_areas = [a for a in app.state.tai_areas if a["code"] != code]

            # Add new area
            app.state.tai_areas.append({
                "code": code,
                "polygon": polygon,
                "cameras": cameras
            })

            # Update feed manager with TAI assignments
            osint = app.state.osint
            if osint._feed_manager:
                for cam_id in cameras:
                    feed = osint._feed_manager.get_feed(cam_id)
                    if feed:
                        feed.tai_code = code

            return {"status": "ok"}
        except Exception as e:
            logger.error(f"Save TAI area error: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

    @app.delete("/api/tai-areas/{code}")
    async def delete_tai_area(code: str):
        # Find cameras in this TAI
        old_area = next((a for a in app.state.tai_areas if a["code"] == code), None)
        if old_area:
            # Clear TAI from cameras
            osint = app.state.osint
            if osint._feed_manager:
                for cam_id in old_area.get("cameras", []):
                    feed = osint._feed_manager.get_feed(cam_id)
                    if feed:
                        feed.tai_code = None

        app.state.tai_areas = [a for a in app.state.tai_areas if a["code"] != code]
        return {"status": "ok"}

    # ============== SHUTDOWN CLEANUP ==============
    @app.on_event("shutdown")
    async def cleanup():
        if app.state.vessel_client:
            await app.state.vessel_client.close()

    # ============== VESSEL TRACKING ==============
    app.state.vessel_client = None
    app.state.vessel_cache = {}
    app.state.vessel_cache_time = 0

    @app.get("/api/vessels")
    async def get_vessels():
        """Get real-time vessel positions from WSDOT API."""
        osint = app.state.osint

        # Check for API key in config (multiple possible locations)
        api_key = (
            osint.config.get("wsdot_api_key") or
            osint.config.get("wsf_api_key") or
            osint.config.get("wsdot_api", {}).get("api_key")
        )
        if not api_key:
            # Return empty - no API key configured
            return {}

        # Use cached data if fresh (within 3 seconds)
        now = time.time()
        if app.state.vessel_cache and (now - app.state.vessel_cache_time) < 3:
            return app.state.vessel_cache

        try:
            # Create client if needed
            if not app.state.vessel_client:
                app.state.vessel_client = WSFVesselsClient(api_key)

            positions = await app.state.vessel_client.get_vessel_locations()

            # Convert to dict format for JSON response
            vessels = {}
            for pos in positions:
                vessels[str(pos.vessel_id)] = {
                    "id": pos.vessel_id,
                    "name": pos.vessel_name,
                    "latitude": pos.latitude,
                    "longitude": pos.longitude,
                    "speed": pos.speed,
                    "heading": pos.heading,
                    "in_service": pos.in_service,
                    "at_dock": pos.at_dock,
                    "departing_terminal": pos.departing_terminal_name,
                    "arriving_terminal": pos.arriving_terminal_name,
                    "eta": pos.eta.isoformat() if pos.eta else None,
                    "vessel_class": pos.vessel_class.name if pos.vessel_class else None,
                    "platform_code": pos.platform_code,
                }

            # Cache the result
            app.state.vessel_cache = vessels
            app.state.vessel_cache_time = now

            # Feed positions into deconfliction engine for cross-source correlation
            _deconfliction.update_api_vessels(vessels)

            # Generate TACREPs for in-service vessels via API tracking
            _generate_api_tacreps(vessels)

            return vessels

        except Exception as e:
            logger.error(f"Failed to fetch vessel positions: {e}")
            # Return cached data if available
            if app.state.vessel_cache:
                return app.state.vessel_cache
            return {}

    @app.get("/api/vessels/{vessel_id}")
    async def get_vessel(vessel_id: str):
        """Get specific vessel details."""
        vessels = await get_vessels()
        if vessel_id not in vessels:
            raise HTTPException(status_code=404, detail=f"Vessel not found: {vessel_id}")
        return vessels[vessel_id]

    @app.post("/api/chatsurfer/test")
    async def test_chatsurfer_connection(request: Request):
        """Test ChatSurfer API connection."""
        try:
            import requests as req
            data = await request.json()

            session = data.get("session")
            room = data.get("room")
            nickname = data.get("nickname", "OSINT_Bot")
            domain = data.get("domain", "chatsurferxmppunclass")
            server_url = data.get("server_url", "https://chatsurfer.nro.mil")

            if not session or not room:
                return JSONResponse({"status": "error", "message": "Missing session or room"}, status_code=400)

            # Try to send a test message
            url = f"{server_url}/api/chatserver/message"
            headers = {
                "cookie": f"SESSION={session}",
                "Content-Type": "application/json"
            }
            payload = {
                "classification": "UNCLASSIFIED//FOUO",
                "message": "[TEST] Connection test from Puget Sound OSINT",
                "domainId": domain,
                "nickName": nickname,
                "roomName": room
            }

            resp = req.post(url, headers=headers, json=payload, verify=False, timeout=10)

            if resp.status_code in [200, 204]:
                return {"status": "ok", "message": "Connection successful"}
            else:
                return JSONResponse({
                    "status": "error",
                    "message": f"Server returned {resp.status_code}: {resp.text[:200]}"
                }, status_code=400)

        except req.exceptions.Timeout:
            return JSONResponse({"status": "error", "message": "Connection timed out"}, status_code=400)
        except req.exceptions.ConnectionError as e:
            return JSONResponse({"status": "error", "message": f"Connection failed: {str(e)}"}, status_code=400)
        except Exception as e:
            logger.error(f"ChatSurfer test error: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

    # Detection endpoints
    _detector = None
    _detection_enabled = False
    _detection_results = {}  # feed_id -> last detection result
    _tacrep_log = []         # Recent TACREP messages for live output
    _tacrep_max_log = 200    # Max entries to keep

    # Deconfliction engine - shared with orchestrator so frame callback detections
    # and API/scan-all detections deconflict against each other
    from ..reporting.deconfliction import TacrepDeconfliction
    from ..reporting.tacrep import ConfidenceLevel
    _deconfliction = osint_app._deconfliction

    @app.get("/api/detection/status")
    async def get_detection_status():
        """Get detection system status."""
        return {
            "enabled": _detection_enabled,
            "model_loaded": _detector is not None,
            "model_path": getattr(_detector, 'model_path', None) if _detector else None,
            "device": getattr(_detector, 'device', 'cpu') if _detector else 'cpu',
            "confidence_threshold": getattr(_detector, 'confidence_threshold', 0.25) if _detector else 0.25,
            "recent_detections": len(_detection_results)
        }

    @app.post("/api/detection/enable")
    async def enable_detection(request: Request):
        """Enable/disable detection and configure settings."""
        nonlocal _detector, _detection_enabled

        data = await request.json()
        enable = data.get("enable", True)
        confidence = data.get("confidence_threshold", 0.25)
        device = data.get("device", "cpu")

        if enable:
            try:
                from ..detection import VesselDetector
                _detector = VesselDetector(
                    model_path="yolov8n.pt",
                    confidence_threshold=confidence,
                    device=device
                )
                _detection_enabled = True
                return {"status": "ok", "message": "Detection enabled"}
            except Exception as e:
                logger.error(f"Failed to enable detection: {e}")
                return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        else:
            _detector = None
            _detection_enabled = False
            return {"status": "ok", "message": "Detection disabled"}

    @app.get("/api/detection/detect/{feed_id}")
    async def detect_in_feed(feed_id: str):
        """Run detection on the latest frame from a camera feed."""
        nonlocal _detection_results

        if not _detection_enabled or _detector is None:
            return JSONResponse({
                "status": "error",
                "message": "Detection not enabled. POST to /api/detection/enable first."
            }, status_code=400)

        if osint_app is None or osint_app.feed_manager is None:
            return JSONResponse({
                "status": "error",
                "message": "Feed manager not available"
            }, status_code=503)

        # Get latest frame
        frame_data = osint_app.feed_manager.get_latest_frame(feed_id)
        if frame_data is None:
            return JSONResponse({
                "status": "error",
                "message": f"No frame available for feed: {feed_id}"
            }, status_code=404)

        frame, timestamp = frame_data

        try:
            result = _detector.detect(frame, camera_id=feed_id)
            _detection_results[feed_id] = result
            return result.to_dict()
        except Exception as e:
            logger.error(f"Detection error for {feed_id}: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    @app.get("/api/detection/detect/{feed_id}/annotated")
    async def detect_annotated(feed_id: str):
        """Run detection and return annotated image. Falls back to raw frame."""
        if osint_app is None or osint_app.feed_manager is None:
            return JSONResponse({"status": "error", "message": "Feed manager not available"}, status_code=503)

        frame_data = osint_app.feed_manager.get_latest_frame(feed_id)
        if frame_data is None:
            return JSONResponse({"status": "error", "message": f"No frame for {feed_id}"}, status_code=404)

        frame, timestamp = frame_data

        try:
            # If detection is active, run detection and annotate
            if _detection_enabled and _detector is not None:
                result, annotated = _detector.detect_and_annotate(frame, camera_id=feed_id)
                _detection_results[feed_id] = result
            else:
                annotated = frame

            # Encode as JPEG
            _, buffer = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
            det_count = result.detection_count if (_detection_enabled and _detector) else 0
            proc_time = round(result.processing_time_ms, 2) if (_detection_enabled and _detector) else 0
            return Response(
                content=buffer.tobytes(),
                media_type="image/jpeg",
                headers={
                    "X-Detection-Count": str(det_count),
                    "X-Processing-Time-Ms": str(proc_time)
                }
            )
        except Exception as e:
            logger.error(f"Detection error for {feed_id}: {e}")
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    @app.get("/api/detection/results")
    async def get_all_detection_results():
        """Get all recent detection results."""
        return {
            feed_id: result.to_dict()
            for feed_id, result in _detection_results.items()
        }

    @app.post("/api/detection/scan-all")
    async def scan_all_feeds():
        """Run detection on all active camera feeds."""
        if not _detection_enabled or _detector is None:
            return JSONResponse({
                "status": "error",
                "message": "Detection not enabled"
            }, status_code=400)

        if osint_app is None or osint_app.feed_manager is None:
            return JSONResponse({
                "status": "error",
                "message": "Feed manager not available"
            }, status_code=503)

        results = {}
        total_detections = 0

        for feed_id, feed in osint_app.feed_manager.feeds.items():
            if not feed.enabled or feed.last_frame is None:
                continue

            try:
                result = _detector.detect(feed.last_frame, camera_id=feed_id)
                _detection_results[feed_id] = result
                results[feed_id] = {
                    "detection_count": result.detection_count,
                    "processing_time_ms": round(result.processing_time_ms, 2)
                }
                total_detections += result.detection_count

                # Auto-generate TACREP with deconfliction
                if result.detection_count > 0 and osint_app._chatsurfer:
                    cam_lat, cam_lon = feed.coordinates
                    # Use feed's assigned TAI, or check if camera is inside a drawn polygon
                    tai = feed.tai_code
                    if not tai and cam_lat and cam_lon:
                        for area in app.state.tai_areas:
                            if _point_in_polygon(cam_lat, cam_lon, area["polygon"]):
                                tai = area["code"]
                                break
                    tai = tai or "UNASSIGNED"

                    for det in result.detections:
                        vessel_key = f"VISUAL_{feed_id}"

                        # Check deconfliction - correlates with API vessels
                        should_send, correlated_name, upgraded_conf = (
                            _deconfliction.should_report(
                                tai=tai,
                                vessel_key=vessel_key,
                                source="visual",
                                camera_lat=cam_lat,
                                camera_lon=cam_lon,
                            )
                        )

                        if not should_send:
                            if correlated_name:
                                logger.debug(
                                    f"Suppressed visual TACREP for {correlated_name} "
                                    f"in {tai} (already reported via API)"
                                )
                            continue

                        # Enrich detection with correlated vessel name
                        det_dict = det.to_dict()
                        if correlated_name:
                            det_dict["vessel_name"] = correlated_name
                        if upgraded_conf:
                            det_dict["confidence"] = 0.95  # CONFIRMED

                        report = osint_app._chatsurfer.report_detection(
                            detection=det_dict,
                            tai=tai,
                            force=False
                        )
                        if report:
                            _deconfliction.record_report(
                                tai=tai,
                                vessel_key=correlated_name or vessel_key,
                                source="visual",
                                platform=report.platform,
                                confidence=report.confidence.value,
                                serial=report.format_serial(),
                                vessel_name=correlated_name,
                                camera_id=feed_id,
                                lat=cam_lat,
                                lon=cam_lon,
                            )
                            source_label = f"visual"
                            if correlated_name:
                                source_label = f"visual+api ({correlated_name})"
                            _log_tacrep(
                                report.to_tacrep_string(),
                                feed_id, feed.name,
                                source=source_label,
                            )

            except Exception as e:
                results[feed_id] = {"error": str(e)}

        return {
            "status": "ok",
            "feeds_scanned": len(results),
            "total_detections": total_detections,
            "results": results
        }

    # Terminal name to TAI code mapping for API vessel tracking
    # Maps WSDOT terminal names to TAI area codes
    _terminal_tai_map = {
        "Seattle": "SEATTLE",
        "Bainbridge Island": "BAINBRIDGE",
        "Bremerton": "BREMERTON",
        "Edmonds": "EDMONDS",
        "Kingston": "KINGSTON",
        "Mukilteo": "MUKILTEO",
        "Clinton": "CLINTON",
        "Fauntleroy": "FAUNTLEROY",
        "Vashon Island": "VASHON",
        "Southworth": "SOUTHWORTH",
        "Point Defiance": "PTDEFIANCE",
        "Tahlequah": "TAHLEQUAH",
        "Anacortes": "ANACORTES",
        "Friday Harbor": "FRIDAYHARBOR",
        "Orcas Island": "ORCAS",
        "Lopez Island": "LOPEZ",
        "Shaw Island": "SHAW",
        "Port Townsend": "PTTOWNSEND",
        "Coupeville": "COUPEVILLE",
    }

    def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
        """Ray-casting point-in-polygon test. Polygon is [[lat, lon], ...]."""
        n = len(polygon)
        if n < 3:
            return False
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i][0], polygon[i][1]
            xj, yj = polygon[j][0], polygon[j][1]
            if ((yi > lon) != (yj > lon)) and \
               (lat < (xj - xi) * (lon - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def _get_vessel_tai(vessel: dict) -> str:
        """Derive TAI code from vessel's position and terminals.

        Lookup order:
          1. User-configured tai_codes (terminal name matching)
          2. Drawn TAI polygons (geographic containment of vessel lat/lon)
          3. Built-in terminal->TAI map (terminal name matching)
          4. Default "PUGETSOUND"
        """
        osint = app.state.osint
        tai_config = osint.config.get("tai_codes", {})

        dep = vessel.get("departing_terminal") or ""
        arr = vessel.get("arriving_terminal") or ""

        # 1. Check user-configured TAI codes (terminal -> code)
        for code, info in tai_config.items():
            terminal = info.get("terminal", "") if isinstance(info, dict) else str(info)
            if terminal and (terminal in dep or terminal in arr):
                return code

        # 2. Check if vessel position falls within a drawn TAI polygon
        lat = vessel.get("latitude", 0)
        lon = vessel.get("longitude", 0)
        if lat and lon:
            for area in app.state.tai_areas:
                if _point_in_polygon(lat, lon, area["polygon"]):
                    return area["code"]

        # 3. Fall back to built-in terminal->TAI mapping
        for terminal, tai in _terminal_tai_map.items():
            if terminal in dep or terminal in arr:
                return tai

        return "PUGETSOUND"

    def _generate_api_tacreps(vessels: dict):
        """Generate TACREPs from vessel API position data with deconfliction."""
        osint = app.state.osint
        if not osint._chatsurfer:
            return

        for vid, vessel in vessels.items():
            # Only report in-service vessels that are underway (not at dock)
            if not vessel.get("in_service", False):
                continue
            if vessel.get("at_dock", True):
                continue

            vessel_name = vessel.get("name", "UNKNOWN")
            platform = vessel.get("platform_code", "UNKNOWN")
            tai = _get_vessel_tai(vessel)
            lat = vessel.get("latitude", 0)
            lon = vessel.get("longitude", 0)

            if not lat or not lon:
                continue

            # Check deconfliction
            should_send, _, upgraded_conf = _deconfliction.should_report(
                tai=tai,
                vessel_key=vessel_name,
                source="api",
            )

            if not should_send:
                continue

            # Build detection dict for TACREP generation
            dep = vessel.get("departing_terminal") or ""
            arr = vessel.get("arriving_terminal") or ""
            speed = vessel.get("speed", 0)

            direction = "OUTBOUND"
            if arr:
                direction = f"EN ROUTE {arr.upper()}"

            remarks_parts = [f"VES {vessel_name.upper()}"]
            if dep and arr:
                remarks_parts.append(f"{dep.upper()} TO {arr.upper()}")
            if speed:
                remarks_parts.append(f"{speed:.1f}KTS")

            report = osint._chatsurfer.tacrep_gen.create_report(
                num_targets=1,
                confidence=ConfidenceLevel.CONFIRMED,
                platform=platform,
                tai=tai,
                remarks=" ".join(remarks_parts),
                vessel_name=vessel_name,
                direction=direction,
            )

            osint._chatsurfer.send_report(report)

            # Record in deconfliction
            _deconfliction.record_report(
                tai=tai,
                vessel_key=vessel_name,
                source="api",
                platform=platform,
                confidence="CONFIRMED",
                serial=report.format_serial(),
                vessel_name=vessel_name,
                lat=lat,
                lon=lon,
            )

            _log_tacrep(
                report.to_tacrep_string(),
                feed_id=None,
                feed_name=vessel_name,
                source="api",
            )

    def _log_tacrep(message: str, feed_id: str = None, feed_name: str = None, source: str = None):
        """Add TACREP to in-memory log for live output."""
        from datetime import datetime, timezone
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "feed_id": feed_id,
            "feed_name": feed_name,
            "source": source or "manual",
        }
        _tacrep_log.append(entry)
        if len(_tacrep_log) > _tacrep_max_log:
            _tacrep_log.pop(0)

    @app.get("/api/tacrep/recent")
    async def get_recent_tacreps(since: str = None):
        """Get recent TACREP messages for live output tab."""
        if since:
            # Return only entries newer than the given timestamp
            return [e for e in _tacrep_log if e["timestamp"] > since]
        return _tacrep_log[-50:]  # Last 50 by default

    @app.post("/api/tacrep/manual")
    async def submit_manual_tacrep(request: Request):
        """Submit a manual TACREP report."""
        data = await request.json()
        osint = app.state.osint

        if not osint._chatsurfer:
            return JSONResponse({"status": "error", "message": "ChatSurfer not initialized"}, status_code=400)

        from ..reporting.tacrep import ConfidenceLevel

        confidence_map = {
            "CONFIRMED": ConfidenceLevel.CONFIRMED,
            "PROBABLE": ConfidenceLevel.PROBABLE,
            "POSSIBLE": ConfidenceLevel.POSSIBLE,
            "UNKNOWN": ConfidenceLevel.UNKNOWN,
        }

        report = osint._chatsurfer.tacrep_gen.create_report(
            num_targets=data.get("num_targets", 1),
            confidence=confidence_map.get(data.get("confidence", "PROBABLE"), ConfidenceLevel.PROBABLE),
            platform=data.get("platform", "UNKNOWN"),
            tai=data.get("tai", "UNASSIGNED"),
            remarks=data.get("remarks", ""),
        )
        osint._chatsurfer.send_report(report)
        _log_tacrep(report.to_tacrep_string())

        return {
            "status": "ok",
            "tacrep": report.to_tacrep_string(),
        }

    @app.get("/api/deconfliction/status")
    async def get_deconfliction_status():
        """Get deconfliction engine status and active report windows."""
        return {
            "suppress_window_sec": _deconfliction.suppress_window_sec,
            "correlation_radius_nm": _deconfliction.correlation_radius_nm,
            "active_reports": _deconfliction.get_active_reports(),
            "api_vessels_cached": len(_deconfliction._api_vessel_cache),
            "total_records": len(_deconfliction._reports),
        }

    return app


def run_server(app: FastAPI, host: str = "0.0.0.0", port: int = 8080):
    """Run the FastAPI server."""
    uvicorn.run(app, host=host, port=port, log_level="info")
