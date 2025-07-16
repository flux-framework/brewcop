/************************************************************\
 * Copyright 2018 Lawrence Livermore National Security, LLC
 * (c.f. AUTHORS, NOTICE.LLNS, COPYING)
 *
 * This file is part of the Flux resource manager framework.
 * For details, see https://github.com/flux-framework.
 *
 * SPDX-License-Identifier: LGPL-3.0
\************************************************************/

/* query.c - send query, receive weight and status */

/* Avery-Berkel 6702-16658 bench scale in ECR mode
 * with default config.
 *
 * Send: "W\r"
 * Expect: VALUE + STATUS
 *     or: STATUS
 * STATUS is (6 bytes) "\rS00\r\003"
 * VALUE is (10 bytes) "\n00.000LB\n" (decimal may move)
 */

/* N.B. Check the interwebs for pi serial port fun.
 * In short, raspi-config can enable the port and disable serial console.
 * On the pi3, there are further complications concerning bluetooth.
 */
const char *path = "/dev/ttyAMA0";


#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <errno.h>

/* Open and config serial port specified by 'path' for 9600, 7E1.
 */
int scale_open (const char *path)
{
	int fd;
	struct termios tio;
	int saved_errno;

	if ((fd = open (path, O_RDWR | O_NOCTTY)) < 0)
		return -1;
	memset (&tio, 0, sizeof (tio));
	tio.c_cflag = B9600 | CS7 | PARENB | CLOCAL | CREAD;
	tio.c_iflag = 0;
	tio.c_oflag = 0;
	tio.c_lflag = 0;
	tio.c_cc[VTIME] = 0; // no timeout
	tio.c_cc[VMIN] = 1; // ready with 1 char
	if (tcflush (fd, TCIFLUSH) < 0)
		goto error_close;
	if (tcsetattr(fd, TCSANOW, &tio) < 0)
		goto error_close;
	return fd;
error_close:
	saved_errno = errno;
	close (fd);
	errno = saved_errno;
	return -1;
}

/* Read a response terminated by ETX (0x03)
 */
int read_response (int fd, char *buf, int size)
{
	int len = 0;
	while (len < size) {
		int n = read (fd, &buf[len], 1);
		if (n < 0)
			return -1;
		if (n == 0) {
			errno = ENODATA;
			return -1;
		}
		len += n;
		if (buf[len - 1] == 0x03) // ETX
			break;
	}
	return len;
}

/* Interpret 6 byte status string.
 */
int parse_status (const char buf[6], const char **message, int *rc)
{
	if (buf[0] != '\n' || buf[1] != 'S' || buf[4] != '\r' || buf[5] != 3)
		return -1;
	if (buf[2] == '0' && buf[3] == '0') {
		*message = "OK";
		*rc = 0;
	}
	else if (buf[2] == '1' && buf[3] == '0') {
		*message = "Weight not stable";
		*rc = -1;
	}
	else if (buf[2] == '2' && buf[3] == '0') {
		*message = "Zero";
		*rc = 0;
	}
	else if (buf[2] == '0' && buf[3] == '1') {
		*message = "Under capacity";
		*rc = -1;
	}
	else if (buf[2] == '0' && buf[3] == '2') {
		*message = "Over capacity";
		*rc = -1;
	}
	else if (buf[2] == '1' && buf[3] == '1') {
		*message = "Under capacity";
		*rc = -1;
	}
	else
		return -1;
	return 0;
}

/* Interpret 10 byte value string, returning weight in pounds
 */
int parse_value (const char buf[10], double *wp)
{
	char *endptr;
	double weight;
	if (buf[0] != '\n' || buf[7] != 'L' || buf[8] != 'B' || buf[9] != '\r')
		return -1;
	weight = strtod (&buf[1], &endptr);
	if (endptr - buf != 7)
		return -1;
	*wp = weight;
	return 0;
}

int main (int argc, char *argv[])
{
	int fd;
	int len;
	char buf[64];
	const char *errmsg;
	char *status;
	double weight;
	int rc;

	fd = scale_open (path);
	if (fd < 0) {
		perror (path);
		return 1;
	}

	/* Query */
	if (write (fd, "W\r", 2) < 0) {
		perror ("write");
		return 1;
	}

	/* Response */
	len = read_response (fd, buf, sizeof (buf));
	if (len < 0) {
		perror ("read response");
		return 1;
	}
	/* Expecting 6 or 16 bytes.
	 * Set 'status' to point to status portion.
	 */
	if (len == 6)
		status = buf;
	else if (len == 16)
		status = buf + 10;
	else {
		fprintf (stderr, "Unexpected response (%d bytes) '%*s'\n", len, len, buf);
		return 1;
	}
	/* Parse status.  If there is a scale error, report it.
	 */
	if (parse_status (status, &errmsg, &rc) < 0) {
		fprintf (stderr, "Error parsing status response\n");
		return 1;
	}
	if (rc < 0) {
		fprintf (stderr, "Scale error: %s\n", errmsg);
		return 1;
	}
	/* Not sure if this can happen.  Got Zero or OK but
	 * without a value.
	 */
	if (len != 16) {
		fprintf (stderr, "Value not returned\n");
		return 1;
	}
	/* Parse the value.
	 * This code assume units are pounds, which seems to be the default
	 * for the test scale.
	 */
	if (parse_value (buf, &weight) < 0) {
		fprintf (stderr, "Error parsing value response\n");
		return 1;
	}
	printf ("%f\n", weight);
	if (close (fd) < 0) {
		perror ("close");
		return 1;
	}
	return 0;
}
