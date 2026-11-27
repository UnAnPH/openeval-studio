#include <set>
#include <algorithm>
#include <iostream>
#include <vector>
#include "graph.h"
#include <queue>
#include <unordered_set>
#define MAX 10000000

struct Path{
    double pathlength;
    std::vector<int> nodes;
};

struct Pathcompare{//true if a>b length
    bool operator()(Path& a, Path& b){
        return a.pathlength>b.pathlength;
    }
};

int HashPath(std::vector<int> path){
    const uint64_t FNV_offset = 1469598103934665603ULL;
    const uint64_t FNV_prime  = 1099511628211ULL;

    uint64_t hash = FNV_offset;

    for (int x : path) {
        hash ^= (uint64_t)x;
        hash *= FNV_prime;
    }

    return hash;
}

double Graph::length_of_path(std::vector<int> path){
    double length=0;
    for(int i=0;i<path.size()-1; i++){
        for(auto &[adjnode, edge_id]: nodes[path[i]].adj){
            if (adjnode==path[i+1]){
                length+=edges[edge_id].length;
                break; 
            }
        }
    }

    return length;
}

std::vector<std::vector<int>> Graph::k_short_exact(int src, int target, int k){

    std::unordered_set<int> visited;
    std::vector<Path> answer_paths;
    std::priority_queue<Path, std::vector<Path>, Pathcompare> potential_paths;
    std::vector<std::vector<int>> final_paths;

    double len1=0;
    std::vector<int> path1=shortest_path_dist(src,target, len1);
    if(path1.size()==0){return {};};
    Path temp={len1,path1};

    answer_paths.push_back(temp);

    visited.insert(HashPath(path1));
    
    for(int i=1;i<k;i++){
       // std::vector<Path> potential_paths;
        //potential_paths.clear();
        //double min_len=MAX;
       // Path minpath;
        std::vector<int> previous_path_nodes= answer_paths[i-1].nodes; // we use prev best path
        int path_size=previous_path_nodes.size(); // sixe of the prvs path


        for(int j=0;j<path_size-1;j++){
            

            std::vector<int> old_path(previous_path_nodes.begin(), previous_path_nodes.begin()+j+1); // go til each node in the prev path

            std::vector<int> edge_removal_temp; // will remove edges that lead to answer path
            int spurnode=old_path[j]; //current node that we check for spur

            for(auto x: answer_paths){
                auto path=x.nodes; // nodes in answer path
                if(path.size()>=j+2){ 
                    if(std::equal(old_path.begin(), old_path.end(), path.begin())){ //match must remove
                        int node1=path[j]; int node2= path[j+1]; // must remove this edge

                        for(auto &[adjnode, edge_id]: nodes[node1].adj){
                            if (adjnode==node2){
                                    edge_removal_temp.push_back(edge_id); // for restoring later
                                    edges[edge_id].blocked=true; //temp block, so it is easier to calc
                            }
                        }
                    }
                }
            }

            double spurlen=-1; //length from spur node to dest
            std::vector<int> spurpath=shortest_path_dist(spurnode,target, spurlen); //path

            if(spurlen>=0){ //it exists
                std::vector<int> newpath=old_path;
                if(spurlen != 0 && spurpath.size() > 1){ 
                newpath.insert(newpath.end(), spurpath.begin()+1, spurpath.end());
                } 
                // If size is 1, it means we are at target or adjacent, logic depends on your graph, 
                // but ensure you don't insert invalid ranges.
                else if (spurlen != 0 && spurpath.size() == 1) {
                    // Handle edge case or do nothing, but do NOT do begin()+1
                }
                int check_visited=HashPath(newpath); //check if it was already visited by other potential paths

                if(visited.count(check_visited)==0){
                    visited.insert(HashPath(newpath)); //insert in visited
                    Path temp={length_of_path(newpath),newpath};
                  //  if(temp.pathlength<min_len){
                    //    min_len=temp.pathlength;
                      //  minpath=temp;
                    //}
                    potential_paths.push(temp); //insert in potential paths
                }
            }
            for(auto x: edge_removal_temp){ ///restoreee
                edges[x].blocked=false;
            }

        }
        if(potential_paths.empty()){ // over
            break;
        }
       // Path minpath=potential_paths[0];
        //double minlen=potential_paths[0].pathlength;
        //int minindex=0;
      //  for (int s=1;s<potential_paths.size();s++){
       //     if(potential_paths[s].pathlength<minlen){
         //       minlen=potential_paths[s].pathlength;
           //     minpath=potential_paths[s];
             //   minindex=s;
            //}
       // }

       // potential_paths.erase(potential_paths.begin()+minindex);
     
        answer_paths.push_back(potential_paths.top());
        potential_paths.pop();

    }

    for(int x=0;x<answer_paths.size(); x++){
        final_paths.push_back(answer_paths[x].nodes);
    }
    return final_paths;
}