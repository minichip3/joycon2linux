/*
 * Joy-Con 2 Linux — C BLE Extension Module
 *
 * Raw HCI/L2CAP/ATT connection to Switch 2 controllers.
 * Bypasses BlueZ D-Bus to avoid scan conflicts on Steam Deck / Bazzite.
 *
 * Requires root privileges for HCI socket operations.
 *
 * Build: gcc -shared -fPIC -O2 -o _ble.so _ble.c -lpython3.x -lbluetooth
 * Headers: bluez-libs-devel (Fedora) or libbluetooth-dev (Debian)
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <netinet/in.h>

#include <bluetooth/bluetooth.h>
#include <bluetooth/hci.h>
#include <bluetooth/hci_lib.h>
#include <bluetooth/l2cap.h>

#ifndef ATT_CID
#define ATT_CID 4  /* Attribute Protocol channel ID */
#endif

#ifndef LE_SCAN_PASSIVE
#define LE_SCAN_PASSIVE 1
#endif
#ifndef LE_SCAN_ACTIVE
#define LE_SCAN_ACTIVE 0
#endif

/* ------------------------------------------------------------------ */
/* Globals                                                             */
/* ------------------------------------------------------------------ */

static int g_hci_fd = -1;       /* HCI socket fd (reserved) */
static int g_l2cap_fd = -1;     /* L2CAP socket fd */
static char g_mac[18];          /* "AA:BB:CC:DD:EE:FF" */
static int g_adapter_idx = 0;   /* adapter index (hci0 -> 0) */
static int g_connected = 0;
static int g_last_errno = 0;   /* errno of the last failed socket op */

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

static int parse_mac(const char *mac_str, bdaddr_t *ba)
{
    uint8_t b[6];
    if (sscanf(mac_str, "%02hhx:%02hhx:%02hhx:%02hhx:%02hhx:%02hhx",
               &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6)
        return -1;
    for (int i = 0; i < 6; i++)
        ba->b[i] = b[5 - i]; /* little-endian */
    return 0;
}

static int get_adapter_index(const char *name)
{
    if (strncmp(name, "hci", 3) == 0)
        return atoi(name + 3);
    return 0;
}

static int get_adapter_mac(const char *adapter_name, char *mac_buf, size_t len)
{
    char path[256];
    snprintf(path, sizeof(path), "/sys/class/bluetooth/%s/address", adapter_name);
    FILE *f = fopen(path, "r");
    if (!f) {
        strcpy(mac_buf, "00:00:00:00:00:00");
        return -1;
    }
    if (fgets(mac_buf, len, f)) {
        /* strip newline */
        char *nl = strchr(mac_buf, '\n');
        if (nl) *nl = '\0';
    }
    fclose(f);
    return 0;
}

/* ------------------------------------------------------------------ */
/* HCI: LE Set Scan                                                    */
/* ------------------------------------------------------------------ */

static int hci_stop_le_scan(int dev_id)
{
    /* Set scan parameters (passive, no filtering) */
    if (hci_le_set_scan_parameters(dev_id, LE_SCAN_PASSIVE, htobs(0x0010),
                                   htobs(0x0010), LE_PUBLIC_ADDRESS, 0, 1000) < 0)
        return -1;
    /* Disable scanning */
    if (hci_le_set_scan_enable(dev_id, 0, 0, 1000) < 0)
        return -1;
    return 0;
}

/* ------------------------------------------------------------------ */
/* L2CAP: Connect                                                      */
/* ------------------------------------------------------------------ */

static int l2cap_connect(bdaddr_t *dst, uint8_t dst_type, uint16_t *acl_handle)
{
    struct sockaddr_l2 addr = {0};
    addr.l2_family = AF_BLUETOOTH;
    addr.l2_cid = htobs(ATT_CID);
    addr.l2_psm = htobs(0);
    bacpy(&addr.l2_bdaddr, dst);

    /* Set dst_type in sockaddr */
    addr.l2_bdaddr_type = dst_type;

    int fd = socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP);
    if (fd < 0) {
        g_last_errno = errno;
        return -1;
    }

    /* Set security */
    uint8_t sec = BT_SECURITY_LOW;
    setsockopt(fd, SOL_BLUETOOTH, BT_SECURITY, &sec, sizeof(sec));

    /* Bind to adapter */
    struct sockaddr_l2 bind_addr = {0};
    bind_addr.l2_family = AF_BLUETOOTH;
    char mac_str[18];
    get_adapter_mac("hci0", mac_str, sizeof(mac_str));
    bdaddr_t adapter_ba;
    parse_mac(mac_str, &adapter_ba);
    bacpy(&bind_addr.l2_bdaddr, &adapter_ba);
    bind_addr.l2_bdaddr_type = LE_PUBLIC_ADDRESS;

    if (bind(fd, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
        g_last_errno = errno;
        close(fd);
        return -2;
    }

    /* Connect */
    fd_set wf;
    FD_ZERO(&wf);
    FD_SET(fd, &wf);

    struct timeval tv = {5, 0};
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        if (errno == EINPROGRESS) {
            int ret = select(fd + 1, NULL, &wf, NULL, &tv);
            if (ret <= 0) {
                close(fd);
                return -3;
            }
            /* Check SO_ERROR */
            int err = 0;
            socklen_t len = sizeof(err);
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
            if (err != 0) {
                close(fd);
                return -err;
            }
        } else {
            g_last_errno = errno;
            close(fd);
            return -4;
        }
    }

    /* Get ACL handle */
    struct hci_conn_info_req *creq = malloc(sizeof(struct hci_conn_info_req) + sizeof(struct hci_conn_info));
    memset(creq, 0, sizeof(struct hci_conn_info_req) + sizeof(struct hci_conn_info));
    creq->type = ACL_LINK;
    bacpy(&creq->bdaddr, dst);

    if (ioctl(fd, HCIGETCONNINFO, (unsigned long)creq) == 0) {
        if (acl_handle)
            *acl_handle = creq->conn_info[0].handle;
    }
    free(creq);

    return fd;
}

/* ------------------------------------------------------------------ */
/* ATT: Read/Write/Subscribe                                           */
/* ------------------------------------------------------------------ */

static int att_read(int fd, uint16_t handle, char *buf, int buf_len)
{
    /* ATT Read Request */
    unsigned char pdu[3];
    pdu[0] = 0x0A; /* ATT_READ_REQ */
    pdu[1] = handle & 0xFF;
    pdu[2] = (handle >> 8) & 0xFF;
    if (write(fd, pdu, 3) != 3) return -1;

    /* Response */
    unsigned char resp[512];
    int n = read(fd, resp, sizeof(resp));
    if (n < 2 || resp[0] != 0x0B) return -1;

    int data_len = n - 1;
    if (data_len > buf_len) data_len = buf_len;
    memcpy(buf, &resp[1], data_len);
    return data_len;
}

static int att_write_cmd(int fd, uint16_t handle, const char *data, int len)
{
    /* ATT Write Command */
    unsigned char *pdu = malloc(3 + len);
    if (!pdu) return -1;
    pdu[0] = 0x52; /* ATT_WRITE_CMD */
    pdu[1] = handle & 0xFF;
    pdu[2] = (handle >> 8) & 0xFF;
    memcpy(&pdu[3], data, len);

    int ret = write(fd, pdu, 3 + len);
    free(pdu);
    return (ret == 3 + len) ? 0 : -1;
}

static int att_write_req(int fd, uint16_t handle, const char *data, int len)
{
    unsigned char *pdu = malloc(3 + len);
    if (!pdu) return -1;
    pdu[0] = 0x12; /* ATT_WRITE_REQ */
    pdu[1] = handle & 0xFF;
    pdu[2] = (handle >> 8) & 0xFF;
    memcpy(&pdu[3], data, len);

    int ret = write(fd, pdu, 3 + len);
    free(pdu);
    if (ret != 3 + len) return -1;

    /* Wait for response */
    unsigned char resp[512];
    fd_set rf;
    FD_ZERO(&rf);
    FD_SET(fd, &rf);
    struct timeval tv = {3, 0};
    if (select(fd + 1, &rf, NULL, NULL, &tv) <= 0)
        return -1;
    return read(fd, resp, sizeof(resp));
}

static int att_subscribe(int fd, uint16_t cccd_handle)
{
    unsigned char data[2];
    data[0] = 0x01; /* notifications */
    data[1] = 0x00;
    return att_write_req(fd, cccd_handle, (char *)data, 2);
}

/* ------------------------------------------------------------------ */
/* GATT Discovery                                                      */
/* ------------------------------------------------------------------ */

static int att_discover_services(int fd, char *buf, int buf_len)
{
    int offset = 0;
    uint16_t start = 0x0001;

    while (start <= 0xFFFF) {
        unsigned char req[7];
        req[0] = 0x10; /* ATT_READ_BY_GROUP_REQ */
        req[1] = start & 0xFF;
        req[2] = (start >> 8) & 0xFF;
        req[3] = 0xFF;
        req[4] = 0x00;
        req[5] = 0x28; /* UUID_PRIMARY */
        req[6] = 0x00;

        if (write(fd, req, 7) != 7) break;

        unsigned char resp[512];
        fd_set rf;
        FD_ZERO(&rf);
        FD_SET(fd, &rf);
        struct timeval tv = {3, 0};
        int nsel = select(fd + 1, &rf, NULL, NULL, &tv);
        if (nsel <= 0) break;

        int n = read(fd, resp, sizeof(resp));
        if (n < 3 || resp[0] != 0x11) break;

        int length = resp[1];
        char *data = (char *)&resp[2];
        uint16_t last_end = 0;

        for (int i = 0; i < n - 2; i += length) {
            uint16_t s, e;
            s = data[i] | (data[i+1] << 8);
            e = data[i+2] | (data[i+3] << 8);
            /* Write: handle_start handle_end uuid */
            if (offset + 20 < buf_len) {
                sprintf(buf + offset, "%u %u 2800\n", s, e);
                offset += strlen(buf + offset);
            }
            last_end = e;
        }

        if (last_end >= 0xFFFF || last_end == 0) break;
        start = last_end + 1;
    }
    return offset;
}

/* ------------------------------------------------------------------ */
/* Python Bindings                                                     */
/* ------------------------------------------------------------------ */

static PyObject *ble_connect(PyObject *self, PyObject *args)
{
    const char *mac_str;
    const char *adapter = "hci0";

    if (!PyArg_ParseTuple(args, "s|s", &mac_str, &adapter))
        return NULL;

    if (g_connected) {
        PyErr_SetString(PyExc_RuntimeError, "Already connected");
        return NULL;
    }

    int dev_id = get_adapter_index(adapter);
    bdaddr_t dst;
    if (parse_mac(mac_str, &dst) < 0) {
        PyErr_SetString(PyExc_ValueError, "Invalid MAC address");
        return NULL;
    }

    /* Stop LE scan */
    hci_stop_le_scan(dev_id);

    /* L2CAP connect */
    uint16_t acl_handle = 0;
    int dst_types[2] = {LE_RANDOM_ADDRESS, LE_PUBLIC_ADDRESS};
    int fd = -1;
    for (int i = 0; i < 2; i++) {
        fd = l2cap_connect(&dst, dst_types[i], &acl_handle);
        if (fd > 0) break;
    }

    if (fd <= 0) {
        const char *what;
        char detail[256];
        if (fd == -3)
            what = "L2CAP connect timeout";
        else if (fd == -4)
            what = "L2CAP connect failed";
        else if (fd == -2)
            what = "bind to adapter failed";
        else
            what = "socket() failed";
        snprintf(detail, sizeof(detail), "%s (errno=%d: %s)",
                 what, g_last_errno, strerror(g_last_errno));
        PyErr_SetString(PyExc_OSError, detail);
        return NULL;
    }

    g_l2cap_fd = fd;
    strncpy(g_mac, mac_str, sizeof(g_mac) - 1);
    g_adapter_idx = dev_id;
    g_connected = 1;

    /* Set non-blocking for notification reading */
    fcntl(fd, F_SETFL, O_NONBLOCK);

    Py_RETURN_TRUE;
}

static PyObject *ble_disconnect(PyObject *self, PyObject *args)
{
    if (!g_connected) Py_RETURN_FALSE;

    if (g_l2cap_fd >= 0) {
        close(g_l2cap_fd);
        g_l2cap_fd = -1;
    }
    g_connected = 0;
    Py_RETURN_TRUE;
}

static PyObject *ble_is_connected(PyObject *self, PyObject *args)
{
    return PyLong_FromLong(g_connected && g_l2cap_fd >= 0);
}

static PyObject *ble_write(PyObject *self, PyObject *args)
{
    unsigned int handle;
    PyObject *data_obj;

    if (!PyArg_ParseTuple(args, "IO", &handle, &data_obj))
        return NULL;

    char *data;
    Py_ssize_t len;
    if (!PyBytes_AsStringAndSize(data_obj, &data, &len))
        return NULL;

    if (!g_connected || g_l2cap_fd < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Not connected");
        return NULL;
    }

    int ret = att_write_cmd(g_l2cap_fd, (uint16_t)handle, data, (int)len);
    if (ret < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *ble_write_req(PyObject *self, PyObject *args)
{
    unsigned int handle;
    PyObject *data_obj;

    if (!PyArg_ParseTuple(args, "IO", &handle, &data_obj))
        return NULL;

    char *data;
    Py_ssize_t len;
    if (!PyBytes_AsStringAndSize(data_obj, &data, &len))
        return NULL;

    if (!g_connected || g_l2cap_fd < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Not connected");
        return NULL;
    }

    int ret = att_write_req(g_l2cap_fd, (uint16_t)handle, data, (int)len);
    if (ret < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *ble_read(PyObject *self, PyObject *args)
{
    unsigned int handle;

    if (!PyArg_ParseTuple(args, "I", &handle))
        return NULL;

    if (!g_connected || g_l2cap_fd < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Not connected");
        return NULL;
    }

    char buf[512];
    int n = att_read(g_l2cap_fd, (uint16_t)handle, buf, sizeof(buf));
    if (n < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    return PyBytes_FromStringAndSize(buf, n);
}

static PyObject *ble_subscribe(PyObject *self, PyObject *args)
{
    unsigned int cccd_handle;

    if (!PyArg_ParseTuple(args, "I", &cccd_handle))
        return NULL;

    if (!g_connected || g_l2cap_fd < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Not connected");
        return NULL;
    }

    int ret = att_subscribe(g_l2cap_fd, (uint16_t)cccd_handle);
    if (ret < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *ble_discover_services(PyObject *self, PyObject *args)
{
    if (!g_connected || g_l2cap_fd < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Not connected");
        return NULL;
    }

    char buf[4096];
    memset(buf, 0, sizeof(buf));
    (void)att_discover_services(g_l2cap_fd, buf, sizeof(buf));
    return PyUnicode_FromString(buf);
}

static PyObject *ble_stop_scan(PyObject *self, PyObject *args)
{
    const char *adapter = "hci0";
    if (!PyArg_ParseTuple(args, "|s", &adapter))
        return NULL;

    int dev_id = get_adapter_index(adapter);
    int ret = hci_stop_le_scan(dev_id);
    if (ret < 0) {
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    Py_RETURN_NONE;
}

/* ------------------------------------------------------------------ */
/* Module Definition                                                   */
/* ------------------------------------------------------------------ */

static PyMethodDef BleMethods[] = {
    {"connect", ble_connect, METH_VARARGS, "Connect to BLE device"},
    {"disconnect", ble_disconnect, METH_NOARGS, "Disconnect"},
    {"is_connected", ble_is_connected, METH_NOARGS, "Check connection state"},
    {"write", ble_write, METH_VARARGS, "Write command (no response)"},
    {"write_req", ble_write_req, METH_VARARGS, "Write request (with response)"},
    {"read", ble_read, METH_VARARGS, "Read characteristic value"},
    {"subscribe", ble_subscribe, METH_VARARGS, "Subscribe to notifications"},
    {"discover_services", ble_discover_services, METH_NOARGS, "Discover GATT services"},
    {"stop_scan", ble_stop_scan, METH_VARARGS, "Stop LE scan via HCI"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef ble_module = {
    PyModuleDef_HEAD_INIT,
    "_ble",
    "Joy-Con 2 Linux BLE module",
    -1,
    BleMethods
};

PyMODINIT_FUNC PyInit__ble(void)
{
    (void)g_hci_fd;  /* reserved for future use */
    return PyModule_Create(&ble_module);
}