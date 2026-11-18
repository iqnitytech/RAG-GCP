# API Documentation: Multi-MCP Platform Integration

This document provides the complete API specification for all 12 tools exposed by the three enterprise MCP servers:
1. `CustomerMCP` (`customer-mcp-server`)
2. `MachineMCP` (`machine-mcp-server`)
3. `TelemetryMCP` (`telemetry-mcp-server`)

---

## 1. Standard Response Envelope

All tools return a uniform JSON-RPC response envelope adhering to Fortune 10 observability standards:

### Success Envelope
```json
{
  "status": "success",
  "data": { ... },
  "metadata": {
    "source": "mock" | "sdk",
    "timestamp": "2026-11-17T12:00:00.000000+00:00",
    "correlation_id": "c1f7a4e2-6d8b-4c3e-9f1a-5b2e7d8c9a0b"
  }
}
```

### Error Envelope
Raw stack traces are never exposed to LLM clients. Errors are safely sanitized and wrapped:
```json
{
  "status": "error",
  "data": {
    "error_type": "DomainValidationError",
    "message": "Invalid customer_id format: 'INVALID'. Expected ^[A-Z0-9\\-]{6,36}$"
  },
  "metadata": {
    "source": "system",
    "timestamp": "2026-11-17T12:00:00.000000+00:00",
    "correlation_id": "c1f7a4e2-6d8b-4c3e-9f1a-5b2e7d8c9a0b"
  }
}
```

---

## 2. CustomerMCP (`customer-mcp-server`)

### Tool 2.1: `customer_get_profile`
Retrieves full 360-degree customer profile, company metadata, tier, billing status, address, and designated account representatives.

- **Parameters**:
  | Name | Type | Required | Description | Example |
  |---|---|---|---|---|
  | `customer_id` | `str` | Yes | Enterprise Customer ID regex `^[A-Z0-9\-]{6,36}$` | `"CUST-001001"` |

- **Sample Request**:
  ```json
  {
    "customer_id": "CUST-001001"
  }
  ```

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "customer_id": "CUST-001001",
      "company_name": "Nexus Industrial Corp",
      "tier": "ENTERPRISE",
      "billing_status": "ACTIVE",
      "region": "NA-EAST",
      "industry": "Manufacturing",
      "address": {
        "street": "100 Nexus Blvd",
        "city": "Boston",
        "state": "MA",
        "country": "USA",
        "postal_code": "02101"
      },
      "account_representatives": [
        {
          "rep_id": "REP-001",
          "name": "Alice Marsh",
          "email": "a.marsh@gcpapp.com",
          "phone": "+1-617-555-0101",
          "role": "Strategic Account Manager"
        }
      ],
      "created_at": "2021-03-15T08:00:00+00:00",
      "updated_at": "2026-01-10T12:00:00+00:00",
      "employee_count": 5200,
      "annual_revenue_usd": 850000000
    },
    "metadata": {
      "source": "mock",
      "timestamp": "2026-11-17T12:00:00.000000+00:00",
      "correlation_id": "3d5f9b1c-..."
    }
  }
  ```

---

### Tool 2.2: `customer_search`
Performs fuzzy/exact search on customer directory with filtering and pagination.

- **Parameters**:
  | Name | Type | Required | Default | Description | Example |
  |---|---|---|---|---|---|
  | `query` | `str` | No | `""` | Substring match against company name | `"Nexus"` |
  | `region` | `str` | No | `null` | Region filter (`NA-EAST`, `EU-WEST`, etc.) | `"NA-EAST"` |
  | `tier` | `str` | No | `null` | Tier filter (`ENTERPRISE`, `PREMIUM`, `STANDARD`) | `"ENTERPRISE"` |
  | `limit` | `int` | No | `10` | Page size limit (1-100) | `10` |
  | `offset` | `int` | No | `0` | Zero-based pagination offset | `0` |

- **Sample Request**:
  ```json
  {
    "query": "Industrial",
    "tier": "ENTERPRISE",
    "limit": 5
  }
  ```

---

### Tool 2.3: `customer_get_entitlements`
Returns active service contracts, SLA levels, purchased features, and expiration dates.

- **Parameters**:
  | Name | Type | Required | Description | Example |
  |---|---|---|---|---|
  | `customer_id` | `str` | Yes | Customer ID | `"CUST-001001"` |

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "customer_id": "CUST-001001",
      "entitlements": [
        {
          "entitlement_id": "ENT-001001",
          "service_name": "Enterprise Industrial Platform",
          "sla_level": "PLATINUM",
          "features": [
            "24/7 Remote Monitoring",
            "Predictive Maintenance AI",
            "On-Site Engineer Dispatch"
          ],
          "expiration_date": "2027-12-31",
          "is_active": true,
          "max_machines": 50
        }
      ],
      "total_active": 1
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```

---

### Tool 2.4: `customer_get_assigned_machines`
Fetches fleet IDs registered to the customer for cross-referencing.

- **Parameters**:
  | Name | Type | Required | Description | Example |
  |---|---|---|---|---|
  | `customer_id` | `str` | Yes | Customer ID | `"CUST-001001"` |
  | `site_id` | `str` | No | Optional Site filter matching `SITE-XXXX` | `"SITE-A1B2"` |

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "customer_id": "CUST-001001",
      "machines": [
        {
          "machine_id": "MCH-A1B2-C3D4",
          "site_id": "SITE-A1B2",
          "model_type": "NX-3000",
          "assigned_date": "2022-04-10"
        },
        {
          "machine_id": "MCH-E5F6-G7H8",
          "site_id": "SITE-A1B2",
          "model_type": "NX-3000",
          "assigned_date": "2022-07-15"
        }
      ],
      "total_count": 2
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```

---

## 3. MachineMCP (`machine-mcp-server`)

### Tool 3.1: `machine_get_details`
Fetches hardware specs, model series, firmware version, commissioning date, and installation location.

- **Parameters**:
  | Name | Type | Required | Description | Example |
  |---|---|---|---|---|
  | `machine_id` | `str` | Yes | Machine ID in `MCH-XXXX-XXXX` format | `"MCH-A1B2-C3D4"` |

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "machine_id": "MCH-A1B2-C3D4",
      "model_type": "INDUSTRIAL-X500",
      "model_series": "X-SERIES-PRO",
      "firmware_version": "v3.4.1",
      "commissioning_date": "2022-04-10",
      "status": "ACTIVE",
      "location": {
        "site_id": "SITE-A1B2",
        "building": "Building A",
        "floor": 1,
        "rack_position": "BAY-04",
        "gps_lat": 42.3601,
        "gps_lon": -71.0589
      },
      "hardware_spec": {
        "cpu_cores": 32,
        "ram_gb": 128,
        "storage_tb": 8.0,
        "gpu_model": "NVIDIA T4 Industrial",
        "network_bandwidth_gbps": 10.0
      },
      "owner_customer_id": "CUST-001001",
      "serial_number": "SN-IND-00101",
      "last_heartbeat": "2026-11-17T06:55:00+00:00"
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```

---

### Tool 3.2: `machine_list_fleet`
Lists enterprise equipment across deployment sites.

- **Parameters**:
  | Name | Type | Required | Default | Description | Example |
  |---|---|---|---|---|---|
  | `model_type` | `str` | No | `null` | Substring model filter | `"INDUSTRIAL"` |
  | `status` | `str` | No | `null` | `ACTIVE`, `MAINTENANCE`, `DECOMMISSIONED` | `"ACTIVE"` |
  | `site_id` | `str` | No | `null` | Site ID (`SITE-XXXX`) | `"SITE-A1B2"` |
  | `page` | `int` | No | `1` | Page number | `1` |
  | `page_size` | `int` | No | `20` | Page size (1-100) | `10` |

---

### Tool 3.3: `machine_get_maintenance_history`
Retrieves service logs, part replacements, technician notes, and expenses.

- **Parameters**:
  | Name | Type | Required | Default | Description | Example |
  |---|---|---|---|---|---|
  | `machine_id` | `str` | Yes | - | Machine ID (`MCH-XXXX-XXXX`) | `"MCH-A1B2-C3D4"` |
  | `limit` | `int` | No | `20` | Maximum records | `5` |

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "machine_id": "MCH-A1B2-C3D4",
      "records": [
        {
          "record_id": "MR-00101",
          "machine_id": "MCH-A1B2-C3D4",
          "performed_at": "2026-03-10T09:00:00+00:00",
          "technician_name": "Marcus Vance",
          "technician_id": "TECH-401",
          "work_type": "PREVENTIVE",
          "description": "Quarterly inspection; lube replenish; thermal calibration.",
          "parts_replaced": ["Air filter AF-12", "Seal ring SR-8"],
          "duration_hours": 3.5,
          "cost_usd": 650.0,
          "next_scheduled": "2026-06-10"
        }
      ],
      "total_cost_usd": 1950.0,
      "total_records": 3
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```

---

### Tool 3.4: `machine_update_operational_state`
Updates machine state with safety transition verification and audit trail logging.

- **Parameters**:
  | Name | Type | Required | Description | Example |
  |---|---|---|---|---|
  | `machine_id` | `str` | Yes | Machine ID (`MCH-XXXX-XXXX`) | `"MCH-A1B2-C3D4"` |
  | `target_state` | `str` | Yes | `ACTIVE`, `MAINTENANCE`, `DECOMMISSIONED` | `"MAINTENANCE"` |
  | `reason` | `str` | Yes | Justification for state transition | `"Scheduled Overhaul Q4"` |

- **Allowed State Transitions**:
  - `ACTIVE` -> `MAINTENANCE`, `DECOMMISSIONED`
  - `MAINTENANCE` -> `ACTIVE`, `DECOMMISSIONED`
  - `DECOMMISSIONED` -> *No transitions allowed (permanent terminal state)*

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "machine_id": "MCH-A1B2-C3D4",
      "previous_state": "ACTIVE",
      "new_state": "MAINTENANCE",
      "reason": "Scheduled Overhaul Q4",
      "updated_at": "2026-11-17T12:00:00+00:00",
      "updated_by": "system",
      "audit_id": "7106c5e4-2f3a-4e1b-8c9d-0a1b2c3d4e5f"
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```

---

## 4. TelemetryMCP (`telemetry-mcp-server`)

### Tool 4.1: `telemetry_get_latest_metrics`
Returns the most recent snapshot of operational sensor values with engineering units.

- **Parameters**:
  | Name | Type | Required | Default | Description | Example |
  |---|---|---|---|---|---|
  | `machine_id` | `str` | Yes | - | Machine ID (`MCH-XXXX-XXXX`) | `"MCH-A1B2-C3D4"` |
  | `metric_names` | `str` | No | `null` | Comma-separated list | `"temperature,vibration,rpm"` |

- **Supported Metric Keys**:
  - `vibration` (`mm/s`)
  - `temperature` (`°C`)
  - `rpm` (`RPM`)
  - `pressure` (`bar`)
  - `power_kw` (`kW`)
  - `humidity` (`%`)
  - `current_amp` (`A`)
  - `voltage` (`V`)

---

### Tool 4.2: `telemetry_query_range`
Queries historical time-series metric data with aggregation downsampling.

- **Parameters**:
  | Name | Type | Required | Default | Description | Example |
  |---|---|---|---|---|---|
  | `machine_id` | `str` | Yes | - | Machine ID | `"MCH-A1B2-C3D4"` |
  | `start_time` | `str` | Yes | - | ISO 8601 start timestamp | `"2026-11-17T08:00:00Z"` |
  | `end_time` | `str` | Yes | - | ISO 8601 end timestamp | `"2026-11-17T12:00:00Z"` |
  | `resolution` | `str` | No | `"5m"` | `1m`, `5m`, `1h`, `6h`, `1d` | `"5m"` |
  | `aggregation` | `str` | No | `"AVG"` | `AVG`, `MAX`, `MIN` | `"AVG"` |

---

### Tool 4.3: `telemetry_get_active_alerts`
Lists active anomaly alarms, triggered thresholds, and recommended remediation protocols.

- **Parameters**:
  | Name | Type | Required | Default | Description | Example |
  |---|---|---|---|---|---|
  | `machine_id` | `str` | No | `null` | Optional machine filter | `"MCH-X9Y8-Z7W6"` |
  | `severity` | `str` | No | `null` | `INFO`, `WARNING`, `CRITICAL` | `"CRITICAL"` |

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "alerts": [
        {
          "alert_id": "ALT-001",
          "machine_id": "MCH-X9Y8-Z7W6",
          "severity": "CRITICAL",
          "metric_name": "vibration",
          "threshold_value": 7.1,
          "actual_value": 8.3,
          "triggered_at": "2026-11-17T10:30:00+00:00",
          "description": "Vibration velocity exceeds ISO 10816 CRITICAL limit (8.3 mm/s > 7.1 mm/s) on servo spindle.",
          "remediation": "Immediate inspection required. Run fault code E-003 and E-002 diagnostics. Halt machine if vibration persists.",
          "is_acknowledged": false
        }
      ],
      "count": 1
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```

---

### Tool 4.4: `telemetry_diagnose_fault_code`
Lookups fault code dictionary, root causes, diagnostic tree, and recommended replacement parts.

- **Parameters**:
  | Name | Type | Required | Description | Example |
  |---|---|---|---|---|
  | `fault_code` | `str` | Yes | Fault code matching `^[A-Z0-9]{1,4}-[0-9]{3,6}$` | `"E-001"` |

- **Available Diagnostic Codes**:
  - `E-001`: Motor Overheat (Critical)
  - `E-002`: Bearing Wear Detected (Warning)
  - `E-003`: Vibration Threshold Exceeded (Critical)
  - `V-001`: Voltage Spike (Warning)
  - `V-002`: Undervoltage Fault (Warning)
  - `P-001`: Pressure Relief Valve Open (Critical)
  - `P-002`: Pressure Sensor Failure (Warning)
  - `C-001`: Communication Timeout (Critical)
  - `C-002`: Network Packet Loss (Info)
  - `F-001`: Fan Motor Failure (Warning)
  - `L-001`: Lubrication System Low (Warning)
  - `L-002`: Lubrication Pump Fault (Warning)
  - `S-001`: Safety Interlock Triggered (Critical)
  - `S-002`: Emergency Stop Activated (Critical)
  - `M-001`: Memory Overflow in Controller (Warning)

- **Sample Success Response**:
  ```json
  {
    "status": "success",
    "data": {
      "fault_code": "E-001",
      "name": "Motor Overheat",
      "description": "Motor core temperature has exceeded safety threshold (>90°C).",
      "root_causes": [
        "Inadequate cooling airflow or clogged fan filter",
        "High duty-cycle operation above rated capacity",
        "Internal bearing friction overload"
      ],
      "diagnostic_steps": [
        "Verify ambient temperature and ventilation intake",
        "Inspect physical fan assembly for obstruction or blade damage",
        "Measure resistance across motor windings",
        "Check current draw on all phases for unbalance",
        "Review thermal cutout sensor calibration"
      ],
      "recommended_parts": [
        "Cooling Fan Assembly CF-200",
        "Thermal Cutout Switch TC-50",
        "Motor Bearing Set MB-100"
      ],
      "severity": "CRITICAL",
      "estimated_repair_hours": 4.0,
      "documentation_url": "https://docs.enterprise.internal/diagnostics/E-001"
    },
    "metadata": { "source": "mock", "timestamp": "...", "correlation_id": "..." }
  }
  ```
