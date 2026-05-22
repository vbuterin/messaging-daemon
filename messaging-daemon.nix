{ config, lib, pkgs, ... }:

let
  messagingDaemon = pkgs.python3.pkgs.buildPythonPackage {
    pname = "messaging-daemon";
    version = "0.1.0";
    format = "pyproject";

    src = pkgs.fetchFromGitHub {
      owner = "vbuterin";
      repo = "messaging-daemon";
      rev = "main";
      sha256 = "0qxgw6cddnpjzdx5wgfvk1irl1clj8i190jj3w36fnv15ry20hzh"; # replace: nix-prefetch-url --unpack https://github.com/vbuterin/messaging-daemon/archive/refs/heads/main.tar.gz
    };

    build-system = [ pkgs.python3.pkgs.setuptools ];
    dependencies = [ pkgs.python3.pkgs.telethon ];
    doCheck = false;
  };
in

{
  environment.systemPackages = [ messagingDaemon ];

  systemd.services.messaging-daemon = {
    description = "Unified Messaging Daemon (Signal + Email)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];

    serviceConfig = {
      Type = "simple";
      User = "messaging-daemon";
      Group = "messaging-daemon";

      ExecStart = "${messagingDaemon}/bin/messaging-daemon run";

      Restart = "on-failure";
      RestartSec = "10s";
    };

    environment = {
      HOME = "/home/messaging-daemon";
      PATH = lib.mkForce "${pkgs.signal-cli}/bin:${messagingDaemon}/bin:/run/current-system/sw/bin";
    };
  };

  users.users.messaging-daemon = {
    isSystemUser = true;
    group = "messaging-daemon";
    home = "/home/messaging-daemon";
    createHome = true;
    description = "Unified messaging daemon service user";
  };

  users.groups.messaging-daemon = {};
}
