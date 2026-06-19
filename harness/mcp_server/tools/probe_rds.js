import {
  RDSClient,
  DescribeDBInstancesCommand,
  DescribeDBParametersCommand,
} from "@aws-sdk/client-rds";
import net from "node:net";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const rdsClient = new RDSClient(awsConfig);

export const probeRdsTools = [
  {
    name: "ace_describe_db_instance",
    description:
      "RDS DescribeDBInstances: return one DB instance's configuration — status, engine/version, instance class, endpoint host/port, publicly_accessible, storage_encrypted, kms_key_id, attached VPC security group IDs, DB subnet group, parameter group name(s), multi_az, master username. Use to diagnose connectivity (SG/subnet), security (encryption/exposure), and config faults.",
    inputSchema: {
      type: "object",
      properties: { db_instance_identifier: { type: "string" } },
      required: ["db_instance_identifier"],
    },
    async handler({ db_instance_identifier } = {}) {
      if (!db_instance_identifier) return { error: "db_instance_identifier is required" };
      try {
        const out = await rdsClient.send(
          new DescribeDBInstancesCommand({ DBInstanceIdentifier: db_instance_identifier })
        );
        const db = (out.DBInstances ?? [])[0];
        if (!db) return { error: `DB instance not found: ${db_instance_identifier}` };
        return {
          identifier: db.DBInstanceIdentifier,
          status: db.DBInstanceStatus ?? null,
          engine: db.Engine ?? null,
          engine_version: db.EngineVersion ?? null,
          instance_class: db.DBInstanceClass ?? null,
          endpoint: db.Endpoint?.Address ?? null,
          port: db.Endpoint?.Port ?? null,
          publicly_accessible: db.PubliclyAccessible ?? null,
          storage_encrypted: db.StorageEncrypted ?? null,
          kms_key_id: db.KmsKeyId ?? null,
          vpc_security_group_ids: (db.VpcSecurityGroups ?? []).map((g) => g.VpcSecurityGroupId),
          db_subnet_group: db.DBSubnetGroup?.DBSubnetGroupName ?? null,
          parameter_groups: (db.DBParameterGroups ?? []).map((p) => p.DBParameterGroupName),
          multi_az: db.MultiAZ ?? null,
          master_username: db.MasterUsername ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_db_parameters",
    description:
      "RDS DescribeDBParameters: list parameters for a named DB parameter group (name, value, source, apply_type). Optionally filter to specific parameter_names (e.g. ['max_connections']). Use to diagnose parameter-group performance faults such as an undersized max_connections.",
    inputSchema: {
      type: "object",
      properties: {
        db_parameter_group_name: { type: "string" },
        parameter_names: { type: "array", items: { type: "string" } },
      },
      required: ["db_parameter_group_name"],
    },
    async handler({ db_parameter_group_name, parameter_names } = {}) {
      if (!db_parameter_group_name) return { error: "db_parameter_group_name is required" };
      try {
        const filter = new Set(parameter_names ?? []);
        const params = [];
        let marker;
        do {
          const out = await rdsClient.send(
            new DescribeDBParametersCommand({
              DBParameterGroupName: db_parameter_group_name,
              Marker: marker,
            })
          );
          for (const p of out.Parameters ?? []) {
            if (filter.size === 0 || filter.has(p.ParameterName)) {
              params.push({
                name: p.ParameterName,
                value: p.ParameterValue ?? null,
                source: p.Source ?? null,
                apply_type: p.ApplyType ?? null,
              });
            }
          }
          marker = out.Marker;
        } while (marker && params.length < 5000);
        return { parameters: params };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_check_db_connectivity",
    description:
      "Open a raw TCP socket to a DB endpoint host:port and report whether it is reachable. outcome is 'connected', 'refused', 'timeout', or 'error'. Use to confirm a connectivity-class fault from the diagnostician's side (pair with ace_describe_security_group and ace_describe_db_instance).",
    inputSchema: {
      type: "object",
      properties: {
        host: { type: "string" },
        port: { type: "integer" },
        timeout_ms: { type: "integer" },
      },
      required: ["host"],
    },
    async handler({ host, port, timeout_ms } = {}) {
      if (!host) return { error: "host is required" };
      const p = port ?? 5432;
      const t = timeout_ms ?? 3000;
      const start = Date.now();
      return await new Promise((resolve) => {
        const sock = new net.Socket();
        let done = false;
        const finish = (outcome, detail) => {
          if (done) return;
          done = true;
          sock.destroy();
          resolve({
            host,
            port: p,
            reachable: outcome === "connected",
            outcome,
            latency_ms: Date.now() - start,
            ...(detail ? { detail } : {}),
          });
        };
        sock.setTimeout(t);
        sock.once("connect", () => finish("connected"));
        sock.once("timeout", () => finish("timeout"));
        sock.once("error", (e) => {
          const outcome = e.code === "ECONNREFUSED" ? "refused" : "error";
          finish(outcome, e.code ?? String(e.message ?? e));
        });
        sock.connect(p, host);
      });
    },
  },
];
