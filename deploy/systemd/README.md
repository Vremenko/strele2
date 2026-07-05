# Strele archive — systemd

## User enota (priporočeno)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/strele-archive.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now strele-archive.service
curl -s http://127.0.0.1:8081/api/health
```

Onemogoči sistemsko enoto, če obstaja (izogni se konfliktu na portu 8081):

```bash
sudo systemctl disable --now strele-archive.service
```

## Sistemska enota

Enota v `/etc/systemd/system/strele-archive.service` — uporabi `User=maximus` in `WantedBy=multi-user.target`.
