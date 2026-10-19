# 杭州活动汇分站（/tour）：源 /opt/tour，waitress 127.0.0.1:8090
# 鉴权复用 /friction 子站口令 qilin/0805（.htpasswd_work）
location = /tour { return 301 /tour/$is_args$args; }

location /tour/ {
    auth_basic "Tour Space";
    auth_basic_user_file /etc/nginx/.htpasswd_work;
    proxy_pass http://127.0.0.1:8090;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
    proxy_connect_timeout 30s;
    proxy_send_timeout 120s;
}
