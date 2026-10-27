#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void live_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_9(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_12(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_35(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_32(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_update_33(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void live_H(double *in_vec, double *out_3455764409279037932);
void live_err_fun(double *nom_x, double *delta_x, double *out_4490125715841635537);
void live_inv_err_fun(double *nom_x, double *true_x, double *out_6511605262224331834);
void live_H_mod_fun(double *state, double *out_6154076936212565674);
void live_f_fun(double *state, double dt, double *out_7091678090989700766);
void live_F_fun(double *state, double dt, double *out_3973798556419173413);
void live_h_4(double *state, double *unused, double *out_7704706258878982927);
void live_H_4(double *state, double *unused, double *out_2868284649321664020);
void live_h_9(double *state, double *unused, double *out_407378892511688135);
void live_H_9(double *state, double *unused, double *out_3109474295951254665);
void live_h_10(double *state, double *unused, double *out_2329326997571466035);
void live_H_10(double *state, double *unused, double *out_4646307868461600041);
void live_h_12(double *state, double *unused, double *out_3109006226655475301);
void live_H_12(double *state, double *unused, double *out_7887741057353625815);
void live_h_35(double *state, double *unused, double *out_7501885500387167171);
void live_H_35(double *state, double *unused, double *out_6234946706694271396);
void live_h_32(double *state, double *unused, double *out_1973591058732288296);
void live_H_32(double *state, double *unused, double *out_679917796999844960);
void live_h_13(double *state, double *unused, double *out_7688881524886791244);
void live_H_13(double *state, double *unused, double *out_2259221340505686235);
void live_h_14(double *state, double *unused, double *out_407378892511688135);
void live_H_14(double *state, double *unused, double *out_3109474295951254665);
void live_h_33(double *state, double *unused, double *out_4355231671475911273);
void live_H_33(double *state, double *unused, double *out_9061240362376422616);
void live_predict(double *in_x, double *in_P, double *in_Q, double dt);
}